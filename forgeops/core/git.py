"""Git-state inspection. Every function here is read-only: none of them
ever mutate the target repository. Generalized from the design proven in
TrendForge's scripts/forgeops/repository_snapshot.py (see
docs/phase2a-porting-notes.md) - always-safe-to-call, stdlib subprocess
only, never raises on a missing/broken git."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from forgeops.core.subprocess_utils import ProcResult, run

GIT_TIMEOUT_SECONDS = 15.0

_URL_CREDENTIALS_RE = re.compile(r"(?P<scheme>\w+://)(?P<user>[^:/?#@\s]+):(?P<password>[^@/?#\s]+)@")


def redact_remote_url(url: str) -> str:
    """Strip embedded userinfo credentials (https://user:pass@host/...)
    from a remote URL. SSH-style remotes (git@host:...) carry no password
    and are returned unchanged."""
    return _URL_CREDENTIALS_RE.sub(lambda m: f"{m.group('scheme')}[REDACTED]@", url)


def git_version() -> str | None:
    result = run(["git", "--version"], timeout=GIT_TIMEOUT_SECONDS)
    return result.stdout.strip() if result.ok else None


def _git(repo_root: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS) -> ProcResult:
    return run(["git", *args], cwd=repo_root, timeout=timeout)


@dataclass(frozen=True)
class BranchState:
    branch: str | None
    detached: bool
    has_commits: bool


def get_branch_state(repo_root: Path) -> BranchState:
    # symbolic-ref succeeds whenever HEAD points at a branch ref, even
    # before that branch has a first commit - so it alone cannot answer
    # "does this repo have commits", only "is HEAD on a named branch".
    symbolic = _git(repo_root, "symbolic-ref", "--short", "-q", "HEAD")
    branch = symbolic.stdout.strip() if symbolic.returncode == 0 else None
    detached = symbolic.returncode != 0
    has_commits = _git(repo_root, "rev-parse", "-q", "--verify", "HEAD").returncode == 0
    if detached and not has_commits:
        # No named branch and no commits (e.g. immediately after `git
        # init` with a detached default) - treat as "not detached", since
        # there is no commit to be detached from yet.
        detached = False
    return BranchState(branch=branch, detached=detached, has_commits=has_commits)


def get_head(repo_root: Path) -> str | None:
    result = _git(repo_root, "rev-parse", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else None


@dataclass(frozen=True)
class Remote:
    name: str
    url: str  # credential-redacted


def get_remotes(repo_root: Path) -> list[Remote]:
    result = _git(repo_root, "remote", "-v")
    if result.returncode != 0:
        return []
    seen: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            name, url = parts[0], parts[1]
            seen.setdefault(name, redact_remote_url(url))
    return [Remote(name=n, url=u) for n, u in seen.items()]


@dataclass(frozen=True)
class AheadBehind:
    upstream: str | None
    ahead: int | None
    behind: int | None


def get_ahead_behind(repo_root: Path) -> AheadBehind:
    upstream_result = _git(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream_result.returncode != 0:
        return AheadBehind(upstream=None, ahead=None, behind=None)
    upstream = upstream_result.stdout.strip()
    counts = _git(repo_root, "rev-list", "--left-right", "--count", f"{upstream}...HEAD")
    if counts.returncode != 0 or not counts.stdout.strip():
        return AheadBehind(upstream=upstream, ahead=None, behind=None)
    try:
        behind_str, ahead_str = counts.stdout.split()
        return AheadBehind(upstream=upstream, ahead=int(ahead_str), behind=int(behind_str))
    except ValueError:
        return AheadBehind(upstream=upstream, ahead=None, behind=None)


@dataclass(frozen=True)
class StatusSummary:
    staged: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.staged or self.modified or self.untracked)


def get_status(repo_root: Path) -> StatusSummary:
    result = _git(repo_root, "status", "--porcelain=v1")
    staged: list[str] = []
    modified: list[str] = []
    untracked: list[str] = []
    if result.returncode != 0:
        return StatusSummary()
    for line in result.stdout.splitlines():
        if not line:
            continue
        index_state, worktree_state = line[0], line[1]
        path = line[3:]
        if index_state == "?" and worktree_state == "?":
            untracked.append(path)
            continue
        if index_state not in (" ", "?"):
            staged.append(path)
        if worktree_state not in (" ", "?"):
            modified.append(path)
    return StatusSummary(staged=staged, modified=modified, untracked=untracked)


def get_ignored_summary(repo_root: Path, limit: int = 50) -> list[str]:
    """Top-level ignored paths only (git collapses ignored directories to
    one entry by default) - a summary, not an exhaustive expansion, so
    this stays fast even when large vendor directories are ignored."""
    result = _git(repo_root, "status", "--porcelain=v1", "--ignored")
    if result.returncode != 0:
        return []
    ignored = [line[3:] for line in result.stdout.splitlines() if line.startswith("!!")]
    return ignored[:limit]


_MERGE_STATE_CHECKS = (
    ("merging", ("MERGE_HEAD",)),
    ("reverting", ("REVERT_HEAD",)),
    ("cherry-picking", ("CHERRY_PICK_HEAD",)),
    ("bisecting", ("BISECT_LOG",)),
)


def get_operation_state(repo_root: Path) -> str:
    """Returns one of: none, merging, reverting, cherry-picking,
    bisecting, rebasing."""
    git_dir_result = _git(repo_root, "rev-parse", "--git-dir")
    if git_dir_result.returncode != 0:
        return "none"
    git_dir = Path(git_dir_result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo_root / git_dir
    if (git_dir / "rebase-merge").is_dir() or (git_dir / "rebase-apply").is_dir():
        return "rebasing"
    for state_name, markers in _MERGE_STATE_CHECKS:
        if any((git_dir / marker).exists() for marker in markers):
            return state_name
    return "none"


@dataclass(frozen=True)
class CommitSummary:
    sha: str
    author: str
    date: str
    subject: str


_LOG_FIELD_SEP = "\x1f"


def get_last_commit(repo_root: Path) -> CommitSummary | None:
    result = _git(
        repo_root,
        "log",
        "-1",
        f"--pretty=format:%H{_LOG_FIELD_SEP}%an{_LOG_FIELD_SEP}%ad{_LOG_FIELD_SEP}%s",
        "--date=iso-strict",
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    parts = result.stdout.split(_LOG_FIELD_SEP)
    if len(parts) != 4:
        return None
    sha, author, date, subject = parts
    return CommitSummary(sha=sha, author=author, date=date, subject=subject)


def is_repo_readable(repo_root: Path) -> bool:
    return _git(repo_root, "rev-parse", "--git-dir").returncode == 0
