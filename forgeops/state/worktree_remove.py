"""Deterministic core logic behind `forgeops worktree remove`
(`forgeops/cli/worktree.py`): a read-only preflight-plan builder
(`build_worktree_remove_plan`) and the single mutating apply step
(`apply_worktree_remove`), following the same split
`forgeops.state.worktree_create` uses for `forgeops worktree create`.
See docs/worktrees.md for the eligibility model, dirty/busy refusal
rules, registry lifecycle, and the branch-deletion opt-in this
implements.

`build_worktree_remove_plan` only ever reads - filesystem/registry
lookups, read-only git plumbing via `forgeops.worktrees.git_worktree`,
the worktree registry, and (only when a plausible match exists) the
process registry - it never writes anything, so the identical function
backs `--dry-run`, the missing-`--confirm` "confirmation required"
response, and a real run's own preflight. `apply_worktree_remove` is
the only function that mutates, and is only ever called after preflight
found zero conflicts and the caller has already confirmed. It revalidates
worktree identity immediately before the single mutating `git worktree
remove` call (closing the preflight/apply TOCTOU gap), never force-removes,
never prunes, and only ever attempts a normal, non-force `git branch -d`
when the caller explicitly opted in."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from forgeops.core.git import get_operation_state, get_status
from forgeops.core.paths import is_protected_reference_path
from forgeops.detectors.process_association import normalize_repo_path
from forgeops.detectors.processes import list_os_processes
from forgeops.security.redact import redact_text
from forgeops.state.runtime_registry import load_registry as load_process_registry
from forgeops.state.worktree_create import ConflictItem
from forgeops.state.worktree_registry import (
    REGISTRY_RELATIVE_PATH,
    STATUS_ACTIVE,
    STATUS_REMOVED,
    WorktreeRecord,
    WorktreeRegistryDocument,
    load_registry,
    save_registry,
)
from forgeops.worktrees.git_worktree import (
    branch_exists,
    delete_branch_safe,
    list_worktrees,
    remove_worktree,
    resolve_commit,
)
from forgeops.worktrees.naming import (
    BRANCH_PREFIX,
    managed_root_for,
    validate_worktree_name,
    worktree_path_for,
)


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


@dataclass(frozen=True)
class BranchDeletePrecheck:
    requested: bool
    eligible: bool
    reason: str | None
    expected_tip: str | None


@dataclass(frozen=True)
class WorktreeRemovePlan:
    repo_root: Path
    name_arg: str
    sanitized_name: str | None
    is_protected: bool
    managed_root: Path
    record: WorktreeRecord | None
    worktree_path: Path | None
    branch: str | None
    branch_is_forgeops_owned: bool
    head_commit: str | None
    is_primary: bool
    locked: bool
    locked_reason: str | None
    branch_delete: BranchDeletePrecheck
    conflicts: tuple[ConflictItem, ...] = field(default_factory=tuple)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


def build_worktree_remove_plan(
    repo_root: Path,
    name_arg: str,
    delete_branch: bool = False,
) -> WorktreeRemovePlan:
    """Read-only preflight: classify every eligibility/dirty/busy
    conflict before any mutation is attempted. Never writes anything,
    never runs a mutating git command."""
    conflicts: list[ConflictItem] = []

    is_protected = is_protected_reference_path(repo_root)
    if is_protected:
        conflicts.append(ConflictItem(
            "protected-reference-repo",
            f"{repo_root} is (or is beneath) the configured read-only reference repository - "
            "forgeops worktree remove will never operate on it",
        ))

    name_error = validate_worktree_name(name_arg)
    sanitized_name = name_arg if name_error is None else None
    if name_error is not None:
        conflicts.append(ConflictItem("invalid-name", name_error))

    managed_root = managed_root_for(repo_root)

    registry = load_registry(repo_root)
    if registry.warning is not None:
        conflicts.append(ConflictItem(
            "registry-malformed",
            f"{REGISTRY_RELATIVE_PATH} could not be read safely, refusing to remove: {registry.warning}",
        ))
        active_records: list[WorktreeRecord] = []
    else:
        active_records = [r for r in registry.records if r.status == STATUS_ACTIVE]

    names_seen: dict[str, int] = {}
    paths_seen: dict[str, int] = {}
    for r in active_records:
        names_seen[r.name] = names_seen.get(r.name, 0) + 1
        paths_seen[_norm(r.path)] = paths_seen.get(_norm(r.path), 0) + 1

    record: WorktreeRecord | None = None
    if sanitized_name is not None and registry.warning is None:
        matches = [r for r in active_records if r.name == sanitized_name]
        if not matches:
            conflicts.append(ConflictItem(
                "not-registered",
                f"'{sanitized_name}' is not an active ForgeOps-registered worktree in {REGISTRY_RELATIVE_PATH}",
            ))
        elif len(matches) > 1:
            conflicts.append(ConflictItem(
                "duplicate-registry-entry",
                f"{REGISTRY_RELATIVE_PATH} has {len(matches)} active records named '{sanitized_name}' - refusing to guess which one to remove",
            ))
        else:
            record = matches[0]
            if paths_seen.get(_norm(record.path), 0) > 1:
                conflicts.append(ConflictItem(
                    "duplicate-registry-entry",
                    f"{REGISTRY_RELATIVE_PATH} has more than one active record for path {record.path} - refusing to guess which one to remove",
                ))

    worktree_path: Path | None = None
    branch: str | None = None
    branch_is_forgeops_owned = False
    head_commit: str | None = None
    is_primary = False
    locked = False
    locked_reason: str | None = None

    if record is not None:
        expected_path = worktree_path_for(repo_root, sanitized_name)
        target_path = Path(record.path)

        if _norm(target_path) != _norm(expected_path):
            conflicts.append(ConflictItem(
                "registry-path-mismatch",
                f"registry path {record.path} does not match the deterministic path {expected_path} for name '{sanitized_name}'",
            ))

        try:
            target_path.resolve().relative_to(managed_root.resolve())
        except ValueError:
            conflicts.append(ConflictItem(
                "outside-managed-root",
                f"{target_path} is not beneath the managed root {managed_root}",
            ))

        if is_protected_reference_path(target_path):
            conflicts.append(ConflictItem(
                "protected-reference-repo",
                f"{target_path} is (or is beneath) the configured read-only reference repository",
            ))

        worktree_path = target_path
        branch = record.branch
        branch_is_forgeops_owned = bool(branch) and branch.startswith(BRANCH_PREFIX)

        if record.task_id is not None:
            conflicts.append(ConflictItem(
                "active-task-ownership",
                f"worktree '{sanitized_name}' is owned by task '{record.task_id}' - refusing to remove",
            ))
        if record.agent_id is not None:
            conflicts.append(ConflictItem(
                "active-agent-ownership",
                f"worktree '{sanitized_name}' is owned by agent '{record.agent_id}' - refusing to remove",
            ))

        listing = list_worktrees(repo_root)
        if not listing.ok:
            conflicts.append(ConflictItem("git-worktree-list-failed", listing.error or "git worktree list failed"))
        else:
            target_norm = _norm(target_path)
            entry = next((e for e in listing.entries if _norm(e.path) == target_norm), None)
            if entry is None:
                conflicts.append(ConflictItem(
                    "stale-registry-entry",
                    f"{target_path} is registered but Git no longer lists it as a worktree - nothing to safely remove",
                ))
            else:
                primary_path = listing.entries[0].path if listing.entries else str(repo_root)
                is_primary = _norm(entry.path) == _norm(primary_path)
                locked = entry.locked
                locked_reason = entry.locked_reason
                head_commit = entry.head

                if is_primary:
                    conflicts.append(ConflictItem(
                        "primary-checkout",
                        "the primary checkout can never be removed by forgeops worktree remove",
                    ))
                if entry.branch != record.branch:
                    conflicts.append(ConflictItem(
                        "identity-mismatch",
                        f"registry branch '{record.branch}' does not match Git's current branch for this "
                        f"worktree ({entry.branch or 'detached'}) - refusing to remove an inconsistent identity",
                    ))
                if locked:
                    conflicts.append(ConflictItem(
                        "worktree-locked",
                        f"worktree is locked{f': {locked_reason}' if locked_reason else ''} - unlock it first "
                        "(forgeops does not implement lock/unlock)",
                    ))

                dirty = get_status(target_path)
                if not dirty.clean:
                    parts = []
                    if dirty.staged:
                        parts.append(f"{len(dirty.staged)} staged")
                    if dirty.modified:
                        parts.append(f"{len(dirty.modified)} modified")
                    if dirty.untracked:
                        parts.append(f"{len(dirty.untracked)} untracked")
                    conflicts.append(ConflictItem(
                        "dirty-worktree",
                        f"worktree has uncommitted changes ({', '.join(parts)}) - refusing to remove",
                    ))

                op_state = get_operation_state(target_path)
                if op_state != "none":
                    conflicts.append(ConflictItem(
                        "git-operation-in-progress",
                        f"a Git operation is in progress in this worktree ({op_state}) - resolve or abort it first",
                    ))

                # Active managed process check - only pay the OS process
                # enumeration cost when a process-registry record plausibly
                # matches this worktree's own path (working_directory is
                # always None on Windows - see forgeops/detectors/processes.py
                # - so an exact registry repository_root match is the only
                # safely-determinable evidence here).
                process_registry = load_process_registry(repo_root)
                target_norm_for_proc = normalize_repo_path(str(target_path))
                candidate_records = [
                    r for r in process_registry.records
                    if normalize_repo_path(r.repository_root) == target_norm_for_proc
                ]
                if candidate_records:
                    discovery = list_os_processes()
                    live_pids = {p.pid for p in discovery.processes}
                    live = [r for r in candidate_records if r.pid in live_pids]
                    if live:
                        pids = ", ".join(str(r.pid) for r in live)
                        conflicts.append(ConflictItem(
                            "active-managed-process",
                            f"a ForgeOps-managed process (PID {pids}) is registered against this worktree "
                            "and still running - refusing to remove",
                        ))

    branch_delete = BranchDeletePrecheck(requested=delete_branch, eligible=False, reason=None, expected_tip=None)
    if delete_branch:
        if record is None or worktree_path is None or branch is None:
            branch_delete = BranchDeletePrecheck(
                requested=True, eligible=False,
                reason="worktree is not an eligible registered worktree", expected_tip=None,
            )
        else:
            reason: str | None = None
            if not branch_is_forgeops_owned:
                reason = (
                    f"branch '{branch}' is not in the ForgeOps-owned namespace ('{BRANCH_PREFIX}*') - "
                    "refusing to delete a non-ForgeOps branch"
                )
            elif not branch_exists(repo_root, branch):
                reason = f"branch '{branch}' does not exist locally"
            else:
                listing = list_worktrees(repo_root)
                elsewhere = listing.ok and any(
                    _norm(e.path) != _norm(worktree_path) and e.branch == branch for e in listing.entries
                )
                if elsewhere:
                    reason = f"branch '{branch}' is checked out in another worktree - refusing to delete"
            expected_tip = resolve_commit(repo_root, branch) if reason is None else None
            branch_delete = BranchDeletePrecheck(
                requested=True, eligible=reason is None, reason=reason, expected_tip=expected_tip,
            )

    return WorktreeRemovePlan(
        repo_root=repo_root,
        name_arg=name_arg,
        sanitized_name=sanitized_name,
        is_protected=is_protected,
        managed_root=managed_root,
        record=record,
        worktree_path=worktree_path,
        branch=branch,
        branch_is_forgeops_owned=branch_is_forgeops_owned,
        head_commit=head_commit,
        is_primary=is_primary,
        locked=locked,
        locked_reason=locked_reason,
        branch_delete=branch_delete,
        conflicts=tuple(conflicts),
    )


@dataclass(frozen=True)
class BranchDeletionOutcome:
    attempted: bool
    deleted: bool
    reason: str | None
    stdout: str
    stderr: str


@dataclass(frozen=True)
class WorktreeRemoveOutcome:
    ok: bool
    stdout: str
    stderr: str
    git_no_longer_lists_it: bool
    directory_removed: bool
    partial_state: dict | None
    registry_written: bool
    registry_error: str | None
    branch_deletion: BranchDeletionOutcome | None


def apply_worktree_remove(repo_root: Path, plan: WorktreeRemovePlan) -> WorktreeRemoveOutcome:
    """Execute the single mutating step this checkpoint performs: `git
    worktree remove <path>` (never `--force`), then (only on verified
    success) mark the registry record `removed` and, only if requested
    and still eligible, attempt a normal `git branch -d`. Callers must
    have already verified `plan.has_conflict` is False. Revalidates
    worktree identity immediately before mutating, to close the gap
    between preflight and this call."""
    assert plan.worktree_path is not None and plan.record is not None

    listing = list_worktrees(repo_root)
    target_norm = _norm(plan.worktree_path)
    entry = next((e for e in listing.entries if _norm(e.path) == target_norm), None) if listing.ok else None
    if entry is None or entry.branch != plan.branch:
        return WorktreeRemoveOutcome(
            ok=False, stdout="", stderr="",
            git_no_longer_lists_it=entry is None,
            directory_removed=not plan.worktree_path.exists(),
            partial_state={
                "reason": "worktree identity changed between preflight and apply - refusing to proceed",
                "git_lists_it": entry is not None,
                "directory_exists": plan.worktree_path.exists(),
            },
            registry_written=False, registry_error=None, branch_deletion=None,
        )

    proc = remove_worktree(repo_root, plan.worktree_path)

    if proc.error is not None or proc.timed_out or proc.returncode != 0:
        post_listing = list_worktrees(repo_root)
        still_listed = post_listing.ok and any(_norm(e.path) == target_norm for e in post_listing.entries)
        stderr = redact_text(proc.stderr or proc.error or f"git worktree remove exited {proc.returncode}")
        return WorktreeRemoveOutcome(
            ok=False, stdout=redact_text(proc.stdout), stderr=stderr,
            git_no_longer_lists_it=not still_listed,
            directory_removed=not plan.worktree_path.exists(),
            partial_state={
                "git_worktree_remove_failed": True,
                "still_listed_in_git": still_listed,
                "directory_exists": plan.worktree_path.exists(),
            },
            registry_written=False, registry_error=None, branch_deletion=None,
        )

    post_listing = list_worktrees(repo_root)
    git_no_longer_lists_it = post_listing.ok and not any(_norm(e.path) == target_norm for e in post_listing.entries)
    directory_removed = not plan.worktree_path.exists()
    ok = bool(git_no_longer_lists_it and directory_removed)

    if not ok:
        return WorktreeRemoveOutcome(
            ok=False, stdout=redact_text(proc.stdout), stderr=redact_text(proc.stderr),
            git_no_longer_lists_it=git_no_longer_lists_it, directory_removed=directory_removed,
            partial_state={
                "git_worktree_remove_reported_success": True,
                "git_no_longer_lists_it": git_no_longer_lists_it,
                "directory_removed": directory_removed,
            },
            registry_written=False, registry_error=None, branch_deletion=None,
        )

    registry_written = False
    registry_error: str | None = None
    fresh_registry = load_registry(repo_root)
    if fresh_registry.warning is not None:
        registry_error = fresh_registry.warning
    else:
        updated_records = [
            WorktreeRecord(**{**r.to_dict(), "status": STATUS_REMOVED}) if r.id == plan.record.id else r
            for r in fresh_registry.records
        ]
        try:
            save_registry(repo_root, WorktreeRegistryDocument(records=updated_records))
            registry_written = True
        except OSError as exc:
            registry_error = str(exc)

    branch_deletion: BranchDeletionOutcome | None = None
    if plan.branch_delete.requested:
        branch_deletion = _apply_branch_deletion(repo_root, plan)

    return WorktreeRemoveOutcome(
        ok=True, stdout=redact_text(proc.stdout), stderr=redact_text(proc.stderr),
        git_no_longer_lists_it=True, directory_removed=True,
        partial_state=None, registry_written=registry_written, registry_error=registry_error,
        branch_deletion=branch_deletion,
    )


def _apply_branch_deletion(repo_root: Path, plan: WorktreeRemovePlan) -> BranchDeletionOutcome:
    precheck = plan.branch_delete
    if not precheck.eligible or plan.branch is None:
        return BranchDeletionOutcome(
            attempted=False, deleted=False,
            reason=precheck.reason or "branch deletion was not eligible", stdout="", stderr="",
        )

    current_tip = resolve_commit(repo_root, plan.branch)
    if current_tip != precheck.expected_tip:
        return BranchDeletionOutcome(
            attempted=False, deleted=False,
            reason="branch tip changed since preflight - refusing to delete", stdout="", stderr="",
        )

    proc = delete_branch_safe(repo_root, plan.branch)
    if proc.returncode == 0:
        return BranchDeletionOutcome(
            attempted=True, deleted=True, reason=None,
            stdout=redact_text(proc.stdout), stderr=redact_text(proc.stderr),
        )
    stderr = redact_text(proc.stderr or proc.error or f"git branch -d exited {proc.returncode}")
    return BranchDeletionOutcome(
        attempted=True, deleted=False,
        reason=f"normal (non-force) branch deletion refused: {stderr.strip()}",
        stdout=redact_text(proc.stdout), stderr=stderr,
    )
