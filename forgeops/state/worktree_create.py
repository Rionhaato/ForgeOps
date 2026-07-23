"""Deterministic core logic behind `forgeops worktree create`
(`forgeops/cli/worktree.py`): a read-only preflight-plan builder
(`build_worktree_create_plan`) and the single mutating writer
(`apply_worktree_create`), following the same split
`forgeops.state.project_init` uses for `forgeops init`. See
docs/worktrees.md for the managed root, branch-naming rule, and
conflict model this implements.

`build_worktree_create_plan` only ever reads (filesystem existence
checks, read-only git plumbing via `forgeops.worktrees.git_worktree`,
reading the worktree registry) - it never writes anything, so the
identical function backs both `--dry-run` and a real run.
`apply_worktree_create` is the only function that mutates, and is only
ever called by `run_worktree_create` after preflight found zero
conflicts."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from forgeops.core.paths import is_protected_reference_path
from forgeops.core.timestamps import Clock, iso_now
from forgeops.security.redact import redact_text
from forgeops.state.worktree_registry import (
    REGISTRY_RELATIVE_PATH,
    STATUS_ACTIVE,
    WorktreeRecord,
    WorktreeRegistryDocument,
    load_registry,
    save_registry,
)
from forgeops.worktrees.git_worktree import (
    WorktreeListResult,
    add_worktree,
    branch_exists,
    is_bare_repository,
    list_worktrees,
    resolve_commit,
)
from forgeops.worktrees.naming import (
    branch_name_for,
    managed_root_for,
    validate_worktree_name,
    worktree_path_for,
)


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


@dataclass(frozen=True)
class ConflictItem:
    key: str
    message: str


@dataclass(frozen=True)
class WorktreeCreatePlan:
    repo_root: Path
    name_arg: str
    sanitized_name: str | None
    is_protected: bool
    is_bare: bool | None
    managed_root: Path
    worktree_path: Path | None
    branch: str | None
    branch_source: str  # "explicit" | "derived"
    base_arg: str | None
    base_ref: str
    base_commit: str | None
    conflicts: tuple[ConflictItem, ...] = field(default_factory=tuple)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


def build_worktree_create_plan(
    repo_root: Path,
    name_arg: str,
    branch_arg: str | None,
    base_arg: str | None,
) -> WorktreeCreatePlan:
    """Read-only preflight: classify every possible conflict before any
    mutation is attempted. Never writes anything, never runs a mutating
    git command."""
    conflicts: list[ConflictItem] = []

    is_protected = is_protected_reference_path(repo_root)
    if is_protected:
        conflicts.append(ConflictItem(
            "protected-reference-repo",
            f"{repo_root} is (or is beneath) the configured read-only reference repository - "
            "forgeops worktree create will never operate on it",
        ))

    is_bare = is_bare_repository(repo_root)
    if is_bare is None:
        conflicts.append(ConflictItem("git-command-failed", "could not determine whether the repository is bare (git rev-parse --is-bare-repository failed)"))
    elif is_bare:
        conflicts.append(ConflictItem("bare-repository", "bare repositories are not supported by forgeops worktree create in this checkpoint"))

    name_error = validate_worktree_name(name_arg)
    sanitized_name = name_arg if name_error is None else None
    if name_error is not None:
        conflicts.append(ConflictItem("invalid-name", name_error))

    managed_root = managed_root_for(repo_root)
    worktree_path = worktree_path_for(repo_root, sanitized_name) if sanitized_name is not None else None

    if worktree_path is not None:
        try:
            worktree_path.resolve().relative_to(managed_root.resolve())
        except ValueError:
            conflicts.append(ConflictItem("outside-managed-root", f"resolved destination {worktree_path} is not beneath the managed root {managed_root}"))
        if worktree_path.exists():
            conflicts.append(ConflictItem("destination-exists", f"{worktree_path} already exists"))

    listing: WorktreeListResult = list_worktrees(repo_root)
    if not listing.ok:
        conflicts.append(ConflictItem("git-worktree-list-failed", listing.error or "git worktree list failed"))
    elif worktree_path is not None:
        target_norm = _norm(worktree_path)
        for entry in listing.entries:
            if _norm(entry.path) == target_norm:
                conflicts.append(ConflictItem("duplicate-worktree", f"{worktree_path} is already registered as a git worktree"))
                break

    branch = branch_arg if branch_arg else (branch_name_for(sanitized_name) if sanitized_name is not None else None)
    branch_source = "explicit" if branch_arg else "derived"

    if branch is not None and listing.ok:
        checked_out_at = [entry.path for entry in listing.entries if entry.branch == branch]
        if checked_out_at:
            conflicts.append(ConflictItem("branch-checked-out-elsewhere", f"branch '{branch}' is already checked out at {checked_out_at[0]}"))
        elif branch_exists(repo_root, branch):
            conflicts.append(ConflictItem(
                "branch-already-exists",
                f"branch '{branch}' already exists - forgeops worktree create only ever creates a "
                "new branch and never reuses or resets an existing one",
            ))

    base_ref = base_arg if base_arg else "HEAD"
    base_commit = resolve_commit(repo_root, base_ref)
    if base_commit is None:
        conflicts.append(ConflictItem("base-ref-not-found", f"base ref '{base_ref}' could not be resolved to a commit"))

    registry = load_registry(repo_root)
    if registry.warning is not None:
        conflicts.append(ConflictItem("registry-malformed", f"{REGISTRY_RELATIVE_PATH} could not be read safely, refusing to create: {registry.warning}"))
    elif worktree_path is not None:
        target_norm = _norm(worktree_path)
        for record in registry.records:
            if record.status == STATUS_ACTIVE and _norm(record.path) == target_norm:
                conflicts.append(ConflictItem("duplicate-registry-entry", f"{worktree_path} is already registered in {REGISTRY_RELATIVE_PATH} as worktree '{record.name}'"))
                break

    return WorktreeCreatePlan(
        repo_root=repo_root,
        name_arg=name_arg,
        sanitized_name=sanitized_name,
        is_protected=is_protected,
        is_bare=is_bare,
        managed_root=managed_root,
        worktree_path=worktree_path,
        branch=branch,
        branch_source=branch_source,
        base_arg=base_arg,
        base_ref=base_ref,
        base_commit=base_commit,
        conflicts=tuple(conflicts),
    )


@dataclass(frozen=True)
class WorktreeCreateOutcome:
    ok: bool
    stdout: str
    stderr: str
    partial_state: dict | None
    registry_written: bool
    registry_error: str | None


def apply_worktree_create(repo_root: Path, plan: WorktreeCreatePlan, clock: Clock | None = None) -> WorktreeCreateOutcome:
    """Execute the single mutating step this checkpoint performs: `git
    worktree add -b <branch> <path> <base_commit>`, then (only on
    success) append one record to the worktree registry. Callers must
    have already verified `plan.has_conflict` is False. On failure, no
    force flags, no `git worktree prune`, no `git branch -D` - the
    caller reports whatever partial state is detected and a manual
    recovery recommendation; nothing is cleaned up automatically."""
    assert plan.worktree_path is not None and plan.branch is not None and plan.base_commit is not None

    plan.managed_root.mkdir(parents=True, exist_ok=True)

    proc = add_worktree(repo_root, plan.worktree_path, plan.branch, plan.base_commit)

    if proc.error is not None or proc.timed_out or proc.returncode != 0:
        directory_created = plan.worktree_path.exists()
        branch_created = branch_exists(repo_root, plan.branch)
        listing = list_worktrees(repo_root)
        registered_in_git = listing.ok and any(_norm(e.path) == _norm(plan.worktree_path) for e in listing.entries)
        partial_state = {
            "directory_created": directory_created,
            "branch_created": branch_created,
            "worktree_registered_in_git": registered_in_git,
        }
        stderr = redact_text(proc.stderr or proc.error or f"git worktree add exited {proc.returncode}")
        return WorktreeCreateOutcome(
            ok=False, stdout=redact_text(proc.stdout), stderr=stderr,
            partial_state=partial_state, registry_written=False, registry_error=None,
        )

    now = iso_now(clock)
    record = WorktreeRecord(
        id=uuid4().hex,
        name=plan.sanitized_name or plan.name_arg,
        path=str(plan.worktree_path),
        branch=plan.branch,
        base_commit=plan.base_commit,
        created_at=now,
        status=STATUS_ACTIVE,
        updated_at=now,
    )

    registry_written = False
    registry_error: str | None = None
    fresh_registry = load_registry(repo_root)
    if fresh_registry.warning is not None:
        registry_error = fresh_registry.warning
    else:
        try:
            save_registry(repo_root, WorktreeRegistryDocument(records=[*fresh_registry.records, record]))
            registry_written = True
        except OSError as exc:
            registry_error = str(exc)

    return WorktreeCreateOutcome(
        ok=True, stdout=redact_text(proc.stdout), stderr=redact_text(proc.stderr),
        partial_state=None, registry_written=registry_written, registry_error=registry_error,
    )
