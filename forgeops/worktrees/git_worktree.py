"""Git worktree plumbing: read-only inspection (`git worktree list
--porcelain` invocation + parsing) and the single mutating call this
checkpoint needs (`git worktree add`). No shell interpolation anywhere -
every git invocation goes through `forgeops.core.subprocess_utils.run`,
which always uses `shell=False` with an explicit argument list.

Parsing is tolerant by construction: `parse_worktree_porcelain` never
raises on unexpected input (a git version emitting an unrecognized
keyword, truncated output, ...) - it returns whatever entries it could
still make sense of, plus a list of warning strings for anything it
didn't recognize, so a single malformed line degrades to a warning
rather than making the whole command fail closed."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from forgeops.core.subprocess_utils import ProcResult, run

GIT_TIMEOUT_SECONDS = 15.0


def _git(repo_root: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS) -> ProcResult:
    return run(["git", *args], cwd=repo_root, timeout=timeout)


@dataclass(frozen=True)
class WorktreeEntry:
    path: str
    head: str | None
    branch: str | None  # short name (refs/heads/ prefix stripped), None if detached/bare
    detached: bool
    bare: bool
    locked: bool
    locked_reason: str | None
    prunable: bool
    prunable_reason: str | None


def _strip_branch_ref(value: str) -> str:
    prefix = "refs/heads/"
    return value[len(prefix):] if value.startswith(prefix) else value


def parse_worktree_porcelain(text: str) -> tuple[list[WorktreeEntry], list[str]]:
    """Parse `git worktree list --porcelain` output. Entries are
    blank-line-separated blocks, each starting with a `worktree <path>`
    line - the path is the remainder of that line verbatim (untouched by
    us), which is exactly how git's porcelain format already handles
    paths containing spaces: no quoting/escaping to undo."""
    entries: list[WorktreeEntry] = []
    warnings: list[str] = []

    path: str | None = None
    head: str | None = None
    branch: str | None = None
    detached = False
    bare = False
    locked = False
    locked_reason: str | None = None
    prunable = False
    prunable_reason: str | None = None

    def _flush() -> None:
        nonlocal path, head, branch, detached, bare, locked, locked_reason, prunable, prunable_reason
        if path is not None:
            entries.append(WorktreeEntry(
                path=path, head=head, branch=branch, detached=detached, bare=bare,
                locked=locked, locked_reason=locked_reason, prunable=prunable, prunable_reason=prunable_reason,
            ))
        path, head, branch = None, None, None
        detached, bare, locked, prunable = False, False, False, False
        locked_reason, prunable_reason = None, None

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line.strip():
            _flush()
            continue
        if line.startswith("worktree "):
            _flush()
            path = line[len("worktree "):]
        elif path is None:
            warnings.append(f"unexpected line before any 'worktree' entry: {line!r}")
        elif line.startswith("HEAD "):
            head = line[len("HEAD "):]
        elif line.startswith("branch "):
            branch = _strip_branch_ref(line[len("branch "):])
        elif line == "detached":
            detached = True
        elif line == "bare":
            bare = True
        elif line == "locked":
            locked = True
        elif line.startswith("locked "):
            locked = True
            locked_reason = line[len("locked "):]
        elif line == "prunable":
            prunable = True
        elif line.startswith("prunable "):
            prunable = True
            prunable_reason = line[len("prunable "):]
        else:
            warnings.append(f"unrecognized worktree porcelain line: {line!r}")
    _flush()

    return entries, warnings


@dataclass(frozen=True)
class WorktreeListResult:
    ok: bool
    entries: list[WorktreeEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def list_worktrees(repo_root: Path) -> WorktreeListResult:
    result = _git(repo_root, "worktree", "list", "--porcelain")
    if result.error is not None:
        return WorktreeListResult(ok=False, error=result.error)
    if result.timed_out:
        return WorktreeListResult(ok=False, error="git worktree list timed out")
    if result.returncode != 0:
        return WorktreeListResult(ok=False, error=result.stderr.strip() or f"git worktree list exited {result.returncode}")
    entries, warnings = parse_worktree_porcelain(result.stdout)
    return WorktreeListResult(ok=True, entries=entries, warnings=warnings)


def is_bare_repository(repo_root: Path) -> bool | None:
    """True/False, or None if git could not answer (caller should treat
    None as a command-execution failure, not as "not bare")."""
    result = _git(repo_root, "rev-parse", "--is-bare-repository")
    if result.returncode != 0 or result.error is not None:
        return None
    value = result.stdout.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def branch_exists(repo_root: Path, branch: str) -> bool:
    result = _git(repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    return result.returncode == 0


def resolve_commit(repo_root: Path, ref: str) -> str | None:
    """Resolve `ref` to a commit SHA, or None if it does not resolve to
    a commit. Uses `<ref>^{commit}` so a resolvable-but-wrong-type ref
    (e.g. a tree/blob) is also rejected, not just a missing ref."""
    result = _git(repo_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def add_worktree(repo_root: Path, worktree_path: Path, branch: str, base_commit: str) -> ProcResult:
    """`git worktree add -b <branch> <worktree_path> <base_commit>` - a
    new branch, from a fixed commit SHA (not a movable ref name), never
    passed through a shell. Callers must have already verified `branch`
    does not exist and `base_commit` resolves to a real commit."""
    return _git(repo_root, "worktree", "add", "-b", branch, str(worktree_path), base_commit, timeout=60.0)


def remove_worktree(repo_root: Path, worktree_path: Path) -> ProcResult:
    """`git worktree remove <worktree_path>` - never `--force`. Git
    itself refuses when the worktree has modified or untracked files, or
    is locked, which is a second, independent safety layer beyond this
    package's own preflight checks (`forgeops.state.worktree_remove`).
    Callers must have already verified the worktree is not the primary
    checkout and is a registered, identity-consistent ForgeOps worktree."""
    return _git(repo_root, "worktree", "remove", str(worktree_path), timeout=60.0)


def delete_branch_safe(repo_root: Path, branch: str) -> ProcResult:
    """`git branch -d <branch>` - normal, non-force local branch deletion
    only. Git itself refuses when the branch is not fully merged;
    callers must never escalate to `-D` on that refusal, and this
    function never accepts a force flag."""
    return _git(repo_root, "branch", "-d", branch)
