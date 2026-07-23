"""Deterministic core logic behind `forgeops task assign`/`forgeops task
unassign` and `forgeops task assign-agent`/`forgeops task
unassign-agent` (see `forgeops/cli/task.py`): read-only preflight-plan
builders and the single mutating apply step for each, following the
same plan/apply split every other mutating ForgeOps command uses.

Ownership is one-to-one and stored only through the existing managed
records this checkpoint's predecessors already reserved a slot for -
`TASK.json.worktree_id`/`TASK.json.agent_id`
(`forgeops/state/task_registry.py`), `WORKTREE_REGISTRY.json`'s
per-record `task_id` (`forgeops/state/worktree_registry.py`), and
`AGENT_REGISTRY.json`'s per-record `assigned_task_id`
(`forgeops/state/agent_registry.py`) - no secondary ownership database.
Both sides of each pair are treated as equally authoritative: a
successful assign/unassign writes both together, rolling back the
first if the second fails, so ownership is never split across the two
records (`TASK_INDEX.json`'s own summary copy remains bookkeeping only,
exactly as `task create`/`task close` already treat it - a failure
updating it after both authoritative records already agree is a
warning, never a failure)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from forgeops.core.paths import is_protected_reference_path
from forgeops.core.timestamps import Clock, iso_now
from forgeops.state.agent_registry import (
    AGENT_REGISTRY_RELATIVE_PATH,
    STATUS_REGISTERED as AGENT_STATUS_REGISTERED,
    AgentRecord,
    AgentRegistryDocument,
    load_agent_registry,
    save_agent_registry,
    validate_agent_id,
)
from forgeops.state.task_registry import (
    INDEX_RELATIVE_PATH,
    TASK_JSON_FILENAME,
    TERMINAL_STATUSES,
    TaskIndexDocument,
    TaskIndexRecord,
    TaskRecord,
    load_index as load_task_index,
    load_task_record,
    save_index as save_task_index,
    save_task_record,
    task_dir_for,
    validate_task_id,
)
from forgeops.state.worktree_create import ConflictItem
from forgeops.state.worktree_registry import (
    REGISTRY_RELATIVE_PATH as WORKTREE_REGISTRY_RELATIVE_PATH,
    STATUS_ACTIVE as WORKTREE_STATUS_ACTIVE,
    STATUS_REMOVED as WORKTREE_STATUS_REMOVED,
    WorktreeRecord,
    WorktreeRegistryDocument,
    load_registry as load_worktree_registry,
    save_registry as save_worktree_registry,
)
from forgeops.worktrees.git_worktree import WorktreeEntry, list_worktrees
from forgeops.worktrees.naming import validate_worktree_name


def _norm(path: str | Path) -> str:
    import os
    return os.path.normcase(os.path.normpath(str(path)))


# --- shared lookups ----------------------------------------------------------


@dataclass(frozen=True)
class _TaskLookup:
    record: TaskRecord | None
    conflicts: tuple[ConflictItem, ...]


def _lookup_task(repo_root: Path, task_id: str) -> _TaskLookup:
    """Deliberately narrower than `forgeops.state.task_validate.validate_task`:
    ownership only cares whether the task's own identity is trustworthy
    (exists, schema-valid, not terminal) - never whether its SPEC.md is
    close-ready (a fresh `draft` task with placeholder Acceptance
    Criteria is a perfectly normal assignment target)."""
    conflicts: list[ConflictItem] = []

    id_error = validate_task_id(task_id)
    if id_error is not None:
        return _TaskLookup(None, (ConflictItem("invalid-task-id", id_error),))

    index = load_task_index(repo_root)
    if index.warning is not None:
        return _TaskLookup(None, (ConflictItem(
            "task-index-malformed", f"{INDEX_RELATIVE_PATH} could not be read safely: {index.warning}",
        ),))

    matches = [r for r in index.records if r.task_id == task_id]
    if not matches:
        return _TaskLookup(None, (ConflictItem("task-not-found", f"no task '{task_id}' found in {INDEX_RELATIVE_PATH}"),))
    if len(matches) > 1:
        conflicts.append(ConflictItem("duplicate-task-id", f"{INDEX_RELATIVE_PATH} has more than one active record for '{task_id}'"))

    task_load = load_task_record(task_dir_for(repo_root, task_id))
    if task_load.warning is not None:
        conflicts.append(ConflictItem("task-json-malformed", f"{TASK_JSON_FILENAME}: {task_load.warning}"))
        return _TaskLookup(None, tuple(conflicts))

    record = task_load.record
    if record.task_id != task_id:
        conflicts.append(ConflictItem("task-id-mismatch", f"TASK.json task_id '{record.task_id}' does not match requested '{task_id}'"))
    if _norm(record.project_root) != _norm(repo_root):
        conflicts.append(ConflictItem("task-project-root-mismatch", f"TASK.json project_root '{record.project_root}' does not match this project ({repo_root})"))

    return _TaskLookup(record, tuple(conflicts))


@dataclass(frozen=True)
class _WorktreeLookup:
    record: WorktreeRecord | None
    entry: WorktreeEntry | None
    conflicts: tuple[ConflictItem, ...]


def _lookup_worktree(repo_root: Path, worktree_name: str) -> _WorktreeLookup:
    conflicts: list[ConflictItem] = []

    name_error = validate_worktree_name(worktree_name)
    if name_error is not None:
        return _WorktreeLookup(None, None, (ConflictItem("invalid-worktree-name", name_error),))

    registry = load_worktree_registry(repo_root)
    if registry.warning is not None:
        return _WorktreeLookup(None, None, (ConflictItem(
            "worktree-registry-malformed", f"{WORKTREE_REGISTRY_RELATIVE_PATH} could not be read safely: {registry.warning}",
        ),))

    matches = [r for r in registry.records if r.name == worktree_name]
    if not matches:
        return _WorktreeLookup(None, None, (ConflictItem("worktree-not-found", f"no worktree named '{worktree_name}' found in {WORKTREE_REGISTRY_RELATIVE_PATH}"),))
    if len(matches) > 1:
        conflicts.append(ConflictItem("duplicate-worktree-registry-entry", f"{WORKTREE_REGISTRY_RELATIVE_PATH} has more than one record named '{worktree_name}'"))

    record = matches[0]
    if record.status == WORKTREE_STATUS_REMOVED:
        conflicts.append(ConflictItem("worktree-removed", f"worktree '{worktree_name}' has been removed (status={record.status})"))
        return _WorktreeLookup(record, None, tuple(conflicts))

    if is_protected_reference_path(Path(record.path)):
        conflicts.append(ConflictItem("worktree-protected-path", f"{record.path} is (or is beneath) the configured read-only reference repository"))

    listing = list_worktrees(repo_root)
    entry: WorktreeEntry | None = None
    if not listing.ok:
        conflicts.append(ConflictItem("git-worktree-list-failed", listing.error or "git worktree list failed"))
    else:
        target_norm = _norm(record.path)
        entry = next((e for e in listing.entries if _norm(e.path) == target_norm), None)
        if entry is None:
            conflicts.append(ConflictItem("worktree-stale", f"'{worktree_name}' is registered but Git no longer lists it as a worktree"))
        else:
            if entry.branch != record.branch:
                conflicts.append(ConflictItem("worktree-identity-mismatch", f"registry branch '{record.branch}' does not match Git's current branch for this worktree ({entry.branch or 'detached'})"))
            if entry.locked:
                conflicts.append(ConflictItem("worktree-locked", f"worktree is locked{f': {entry.locked_reason}' if entry.locked_reason else ''}"))

    return _WorktreeLookup(record, entry, tuple(conflicts))


@dataclass(frozen=True)
class _AgentLookup:
    record: AgentRecord | None
    conflicts: tuple[ConflictItem, ...]


def _lookup_agent(repo_root: Path, agent_id: str) -> _AgentLookup:
    """Agent-intrinsic eligibility only (exists, registry valid, not
    disabled) - whether it is already assigned to something is an
    ownership question the plan builder adds on top, mirroring
    `_lookup_worktree`'s own split."""
    conflicts: list[ConflictItem] = []

    id_error = validate_agent_id(agent_id)
    if id_error is not None:
        return _AgentLookup(None, (ConflictItem("invalid-agent-id", id_error),))

    registry = load_agent_registry(repo_root)
    if registry.warning is not None:
        return _AgentLookup(None, (ConflictItem(
            "agent-registry-malformed", f"{AGENT_REGISTRY_RELATIVE_PATH} could not be read safely: {registry.warning}",
        ),))

    matches = [r for r in registry.records if r.agent_id == agent_id]
    if not matches:
        return _AgentLookup(None, (ConflictItem("agent-not-found", f"no agent '{agent_id}' found in {AGENT_REGISTRY_RELATIVE_PATH}"),))
    if len(matches) > 1:
        conflicts.append(ConflictItem("duplicate-agent-id", f"{AGENT_REGISTRY_RELATIVE_PATH} has more than one record for '{agent_id}'"))

    record = matches[0]
    if record.status != AGENT_STATUS_REGISTERED:
        conflicts.append(ConflictItem("agent-disabled", f"agent '{agent_id}' has status '{record.status}' - a disabled agent cannot be assigned"))

    return _AgentLookup(record, tuple(conflicts))


# --- task assign ---------------------------------------------------------------


@dataclass(frozen=True)
class TaskAssignPlan:
    repo_root: Path
    task_id: str
    worktree_name: str
    task_record: TaskRecord | None
    worktree_record: WorktreeRecord | None
    conflicts: tuple[ConflictItem, ...] = field(default_factory=tuple)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


def build_task_assign_plan(repo_root: Path, task_id: str, worktree_name: str) -> TaskAssignPlan:
    """Read-only preflight, shared by `--dry-run` and a real run, and
    re-run verbatim by `apply_task_assign` immediately before mutating
    to close the preflight/apply TOCTOU gap. Never writes anything."""
    conflicts: list[ConflictItem] = []

    task_lookup = _lookup_task(repo_root, task_id)
    conflicts.extend(task_lookup.conflicts)

    worktree_lookup = _lookup_worktree(repo_root, worktree_name)
    conflicts.extend(worktree_lookup.conflicts)

    if task_lookup.record is not None:
        if task_lookup.record.status in TERMINAL_STATUSES:
            conflicts.append(ConflictItem("task-already-terminal", f"task status is '{task_lookup.record.status}' - a terminal task cannot be assigned a worktree"))
        if task_lookup.record.worktree_id is not None:
            conflicts.append(ConflictItem("task-already-assigned", f"task '{task_id}' is already assigned to worktree '{task_lookup.record.worktree_id}'"))

    if worktree_lookup.record is not None and worktree_lookup.record.status == WORKTREE_STATUS_ACTIVE:
        if worktree_lookup.record.task_id is not None:
            conflicts.append(ConflictItem("worktree-already-assigned", f"worktree '{worktree_name}' is already assigned to task '{worktree_lookup.record.task_id}'"))

    return TaskAssignPlan(
        repo_root=repo_root, task_id=task_id, worktree_name=worktree_name,
        task_record=task_lookup.record, worktree_record=worktree_lookup.record,
        conflicts=tuple(conflicts),
    )


@dataclass(frozen=True)
class OwnershipApplyOutcome:
    ok: bool
    task_json_written: bool
    worktree_registry_written: bool
    index_written: bool
    index_error: str | None
    partial_state: dict | None


def apply_task_assign(repo_root: Path, plan: TaskAssignPlan, clock: Clock | None = None) -> OwnershipApplyOutcome:
    """TASK.json and WORKTREE_REGISTRY.json are updated as the atomic
    pair (both authoritative for ownership) - if the second write fails
    after the first succeeded, the first is rolled back (best-effort)
    and the whole call reports failure, never a partial assignment.
    TASK_INDEX.json is updated last and its own failure is reported as a
    warning, not a failure, mirroring `task create`/`task close`."""
    assert plan.task_record is not None and plan.worktree_record is not None

    fresh_plan = build_task_assign_plan(repo_root, plan.task_id, plan.worktree_name)
    if fresh_plan.has_conflict:
        return OwnershipApplyOutcome(
            ok=False, task_json_written=False, worktree_registry_written=False,
            index_written=False, index_error=None,
            partial_state={
                "reason": "ownership state changed between preflight and apply - refusing to proceed",
                "conflicts": [{"key": c.key, "message": c.message} for c in fresh_plan.conflicts],
            },
        )

    now = iso_now(clock)
    task_dir = task_dir_for(repo_root, plan.task_id)
    original_task_record = plan.task_record
    updated_task_record = TaskRecord(**{**original_task_record.to_dict(), "worktree_id": plan.worktree_name, "updated_at": now})

    try:
        save_task_record(task_dir, updated_task_record)
    except OSError as exc:
        return OwnershipApplyOutcome(
            ok=False, task_json_written=False, worktree_registry_written=False,
            index_written=False, index_error=None,
            partial_state={"error": str(exc), "task_json_written": False},
        )

    fresh_registry = load_worktree_registry(repo_root)
    if fresh_registry.warning is not None:
        _rollback_task_record(task_dir, original_task_record)
        return OwnershipApplyOutcome(
            ok=False, task_json_written=False, worktree_registry_written=False,
            index_written=False, index_error=None,
            partial_state={"error": fresh_registry.warning, "task_json_written": True, "rolled_back": True},
        )

    updated_wt_records = [
        WorktreeRecord(**{**r.to_dict(), "task_id": plan.task_id, "updated_at": now}) if r.id == plan.worktree_record.id else r
        for r in fresh_registry.records
    ]
    try:
        save_worktree_registry(repo_root, WorktreeRegistryDocument(
            schema_version=fresh_registry.schema_version, records=updated_wt_records,
        ))
    except OSError as exc:
        _rollback_task_record(task_dir, original_task_record)
        return OwnershipApplyOutcome(
            ok=False, task_json_written=False, worktree_registry_written=False,
            index_written=False, index_error=None,
            partial_state={"error": str(exc), "task_json_written": True, "rolled_back": True},
        )

    index_written, index_error = _update_task_index_ownership(repo_root, plan.task_id, plan.worktree_name, now)
    return OwnershipApplyOutcome(
        ok=True, task_json_written=True, worktree_registry_written=True,
        index_written=index_written, index_error=index_error, partial_state=None,
    )


# --- task unassign -------------------------------------------------------------


@dataclass(frozen=True)
class TaskUnassignPlan:
    repo_root: Path
    task_id: str
    task_record: TaskRecord | None
    worktree_record: WorktreeRecord | None
    conflicts: tuple[ConflictItem, ...] = field(default_factory=tuple)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


def build_task_unassign_plan(repo_root: Path, task_id: str) -> TaskUnassignPlan:
    """Read-only preflight for `task unassign`. A task with no current
    assignment is itself a conflict (`task-not-assigned`) - there is
    nothing to unassign, and this checkpoint never treats "no-op" as a
    silent success."""
    task_lookup = _lookup_task(repo_root, task_id)
    conflicts = list(task_lookup.conflicts)

    worktree_record: WorktreeRecord | None = None
    if task_lookup.record is not None:
        worktree_name = task_lookup.record.worktree_id
        if worktree_name is None:
            conflicts.append(ConflictItem("task-not-assigned", f"task '{task_id}' has no assigned worktree - nothing to unassign"))
        else:
            wt_lookup = _lookup_worktree_for_unassign(repo_root, worktree_name)
            worktree_record = wt_lookup.record
            conflicts.extend(wt_lookup.conflicts)

    return TaskUnassignPlan(
        repo_root=repo_root, task_id=task_id, task_record=task_lookup.record,
        worktree_record=worktree_record, conflicts=tuple(conflicts),
    )


@dataclass(frozen=True)
class _WorktreeUnassignLookup:
    record: WorktreeRecord | None
    conflicts: tuple[ConflictItem, ...]


def _lookup_worktree_for_unassign(repo_root: Path, worktree_name: str) -> _WorktreeUnassignLookup:
    """Looser than `_lookup_worktree`: unassign must still succeed even
    when the worktree was independently removed (a common, expected
    path - `worktree remove` never clears a task's ownership itself) or
    is missing from the registry entirely - only TASK.json is
    guaranteed to be touched in that case, reported via
    `worktree_registry_written=False` rather than refused outright."""
    registry = load_worktree_registry(repo_root)
    if registry.warning is not None:
        return _WorktreeUnassignLookup(None, (ConflictItem(
            "worktree-registry-malformed", f"{WORKTREE_REGISTRY_RELATIVE_PATH} could not be read safely: {registry.warning}",
        ),))
    matches = [r for r in registry.records if r.name == worktree_name]
    if len(matches) > 1:
        return _WorktreeUnassignLookup(None, (ConflictItem("duplicate-worktree-registry-entry", f"{WORKTREE_REGISTRY_RELATIVE_PATH} has more than one record named '{worktree_name}'"),))
    return _WorktreeUnassignLookup(matches[0] if matches else None, ())


def apply_task_unassign(repo_root: Path, plan: TaskUnassignPlan, clock: Clock | None = None) -> OwnershipApplyOutcome:
    """Mirrors `apply_task_assign`'s atomicity model. If the worktree
    registry no longer has a matching record at all (e.g. the worktree
    was removed independently since assignment), TASK.json is still
    cleared - there is nothing left to roll it back against - and that
    is reported plainly via `worktree_registry_written=False`, not as a
    failure."""
    assert plan.task_record is not None

    fresh_plan = build_task_unassign_plan(repo_root, plan.task_id)
    if fresh_plan.has_conflict:
        return OwnershipApplyOutcome(
            ok=False, task_json_written=False, worktree_registry_written=False,
            index_written=False, index_error=None,
            partial_state={
                "reason": "ownership state changed between preflight and apply - refusing to proceed",
                "conflicts": [{"key": c.key, "message": c.message} for c in fresh_plan.conflicts],
            },
        )

    now = iso_now(clock)
    task_dir = task_dir_for(repo_root, plan.task_id)
    original_task_record = plan.task_record
    updated_task_record = TaskRecord(**{**original_task_record.to_dict(), "worktree_id": None, "updated_at": now})

    try:
        save_task_record(task_dir, updated_task_record)
    except OSError as exc:
        return OwnershipApplyOutcome(
            ok=False, task_json_written=False, worktree_registry_written=False,
            index_written=False, index_error=None,
            partial_state={"error": str(exc), "task_json_written": False},
        )

    worktree_registry_written = False
    if plan.worktree_record is not None:
        fresh_registry = load_worktree_registry(repo_root)
        if fresh_registry.warning is not None:
            _rollback_task_record(task_dir, original_task_record)
            return OwnershipApplyOutcome(
                ok=False, task_json_written=False, worktree_registry_written=False,
                index_written=False, index_error=None,
                partial_state={"error": fresh_registry.warning, "task_json_written": True, "rolled_back": True},
            )
        updated_wt_records = [
            WorktreeRecord(**{**r.to_dict(), "task_id": None, "updated_at": now}) if r.id == plan.worktree_record.id else r
            for r in fresh_registry.records
        ]
        try:
            save_worktree_registry(repo_root, WorktreeRegistryDocument(
                schema_version=fresh_registry.schema_version, records=updated_wt_records,
            ))
            worktree_registry_written = True
        except OSError as exc:
            _rollback_task_record(task_dir, original_task_record)
            return OwnershipApplyOutcome(
                ok=False, task_json_written=False, worktree_registry_written=False,
                index_written=False, index_error=None,
                partial_state={"error": str(exc), "task_json_written": True, "rolled_back": True},
            )

    index_written, index_error = _update_task_index_ownership(repo_root, plan.task_id, None, now)
    return OwnershipApplyOutcome(
        ok=True, task_json_written=True, worktree_registry_written=worktree_registry_written,
        index_written=index_written, index_error=index_error, partial_state=None,
    )


# --- task assign-agent ----------------------------------------------------------


@dataclass(frozen=True)
class TaskAssignAgentPlan:
    repo_root: Path
    task_id: str
    agent_id: str
    task_record: TaskRecord | None
    agent_record: AgentRecord | None
    conflicts: tuple[ConflictItem, ...] = field(default_factory=tuple)
    warnings: tuple[ConflictItem, ...] = field(default_factory=tuple)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


def build_task_assign_agent_plan(repo_root: Path, task_id: str, agent_id: str) -> TaskAssignAgentPlan:
    """Read-only preflight, shared by `--dry-run` and a real run, and
    re-run verbatim by `apply_task_assign_agent` immediately before
    mutating to close the preflight/apply TOCTOU gap. A task with no
    assigned worktree is a non-blocking warning only - agent and
    worktree ownership are independent fields."""
    conflicts: list[ConflictItem] = []
    warnings: list[ConflictItem] = []

    task_lookup = _lookup_task(repo_root, task_id)
    conflicts.extend(task_lookup.conflicts)

    agent_lookup = _lookup_agent(repo_root, agent_id)
    conflicts.extend(agent_lookup.conflicts)

    if task_lookup.record is not None:
        if task_lookup.record.status in TERMINAL_STATUSES:
            conflicts.append(ConflictItem("task-already-terminal", f"task status is '{task_lookup.record.status}' - a terminal task cannot be assigned an agent"))
        if task_lookup.record.agent_id is not None:
            conflicts.append(ConflictItem("task-already-has-agent", f"task '{task_id}' is already assigned to agent '{task_lookup.record.agent_id}'"))
        if not task_lookup.record.worktree_id:
            warnings.append(ConflictItem("task-has-no-worktree", f"task '{task_id}' has no assigned worktree yet - agent and worktree ownership are independent"))

        index = load_task_index(repo_root)
        if index.warning is None:
            idx_matches = [r for r in index.records if r.task_id == task_id]
            if idx_matches and idx_matches[0].agent_id != task_lookup.record.agent_id:
                conflicts.append(ConflictItem("task-index-mismatch", f"{INDEX_RELATIVE_PATH} agent_id does not match TASK.json for '{task_id}'"))

    if agent_lookup.record is not None and agent_lookup.record.status == AGENT_STATUS_REGISTERED:
        if agent_lookup.record.assigned_task_id is not None:
            conflicts.append(ConflictItem("agent-already-assigned", f"agent '{agent_id}' is already assigned to task '{agent_lookup.record.assigned_task_id}'"))

    return TaskAssignAgentPlan(
        repo_root=repo_root, task_id=task_id, agent_id=agent_id,
        task_record=task_lookup.record, agent_record=agent_lookup.record,
        conflicts=tuple(conflicts), warnings=tuple(warnings),
    )


@dataclass(frozen=True)
class AgentOwnershipApplyOutcome:
    ok: bool
    task_json_written: bool
    agent_registry_written: bool
    index_written: bool
    index_error: str | None
    partial_state: dict | None


def apply_task_assign_agent(repo_root: Path, plan: TaskAssignAgentPlan, clock: Clock | None = None) -> AgentOwnershipApplyOutcome:
    """TASK.json and AGENT_REGISTRY.json are updated as the atomic pair
    (both authoritative for agent ownership) - if the second write fails
    after the first succeeded, the first is rolled back (best-effort)
    and the whole call reports failure, never a partial assignment.
    TASK_INDEX.json is updated last and its own failure is reported as a
    warning, not a failure, mirroring `apply_task_assign`."""
    assert plan.task_record is not None and plan.agent_record is not None

    fresh_plan = build_task_assign_agent_plan(repo_root, plan.task_id, plan.agent_id)
    if fresh_plan.has_conflict:
        return AgentOwnershipApplyOutcome(
            ok=False, task_json_written=False, agent_registry_written=False,
            index_written=False, index_error=None,
            partial_state={
                "reason": "agent ownership state changed between preflight and apply - refusing to proceed",
                "conflicts": [{"key": c.key, "message": c.message} for c in fresh_plan.conflicts],
            },
        )

    now = iso_now(clock)
    task_dir = task_dir_for(repo_root, plan.task_id)
    original_task_record = plan.task_record
    updated_task_record = TaskRecord(**{**original_task_record.to_dict(), "agent_id": plan.agent_id, "updated_at": now})

    try:
        save_task_record(task_dir, updated_task_record)
    except OSError as exc:
        return AgentOwnershipApplyOutcome(
            ok=False, task_json_written=False, agent_registry_written=False,
            index_written=False, index_error=None,
            partial_state={"error": str(exc), "task_json_written": False},
        )

    fresh_registry = load_agent_registry(repo_root)
    if fresh_registry.warning is not None:
        _rollback_task_record(task_dir, original_task_record)
        return AgentOwnershipApplyOutcome(
            ok=False, task_json_written=False, agent_registry_written=False,
            index_written=False, index_error=None,
            partial_state={"error": fresh_registry.warning, "task_json_written": True, "rolled_back": True},
        )

    updated_agent_records = [
        AgentRecord(**{**r.to_dict(), "assigned_task_id": plan.task_id, "updated_at": now}) if r.agent_id == plan.agent_record.agent_id else r
        for r in fresh_registry.records
    ]
    try:
        save_agent_registry(repo_root, AgentRegistryDocument(
            schema_version=fresh_registry.schema_version, records=updated_agent_records,
        ))
    except OSError as exc:
        _rollback_task_record(task_dir, original_task_record)
        return AgentOwnershipApplyOutcome(
            ok=False, task_json_written=False, agent_registry_written=False,
            index_written=False, index_error=None,
            partial_state={"error": str(exc), "task_json_written": True, "rolled_back": True},
        )

    index_written, index_error = _update_task_index_agent_ownership(repo_root, plan.task_id, plan.agent_id, now)
    return AgentOwnershipApplyOutcome(
        ok=True, task_json_written=True, agent_registry_written=True,
        index_written=index_written, index_error=index_error, partial_state=None,
    )


# --- task unassign-agent --------------------------------------------------------


@dataclass(frozen=True)
class TaskUnassignAgentPlan:
    repo_root: Path
    task_id: str
    task_record: TaskRecord | None
    agent_record: AgentRecord | None
    conflicts: tuple[ConflictItem, ...] = field(default_factory=tuple)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


def build_task_unassign_agent_plan(repo_root: Path, task_id: str) -> TaskUnassignAgentPlan:
    """Read-only preflight for `task unassign-agent`. A task with no
    current agent assignment is itself a conflict (`task-not-assigned-agent`)
    - double-unassigning is refused, never a silent no-op, matching the
    same choice already made for `task unassign` (worktree ownership)."""
    task_lookup = _lookup_task(repo_root, task_id)
    conflicts = list(task_lookup.conflicts)

    agent_record: AgentRecord | None = None
    if task_lookup.record is not None:
        agent_id = task_lookup.record.agent_id
        if agent_id is None:
            conflicts.append(ConflictItem("task-not-assigned-agent", f"task '{task_id}' has no assigned agent - nothing to unassign"))
        else:
            lookup = _lookup_agent_for_unassign(repo_root, agent_id)
            agent_record = lookup.record
            conflicts.extend(lookup.conflicts)

    return TaskUnassignAgentPlan(
        repo_root=repo_root, task_id=task_id, task_record=task_lookup.record,
        agent_record=agent_record, conflicts=tuple(conflicts),
    )


@dataclass(frozen=True)
class _AgentUnassignLookup:
    record: AgentRecord | None
    conflicts: tuple[ConflictItem, ...]


def _lookup_agent_for_unassign(repo_root: Path, agent_id: str) -> _AgentUnassignLookup:
    """Looser than `_lookup_agent`: unassign must still succeed even if
    the agent record is missing entirely - only TASK.json is guaranteed
    to be touched in that case, reported via `agent_registry_written=False`
    rather than refused outright, mirroring `_lookup_worktree_for_unassign`."""
    registry = load_agent_registry(repo_root)
    if registry.warning is not None:
        return _AgentUnassignLookup(None, (ConflictItem(
            "agent-registry-malformed", f"{AGENT_REGISTRY_RELATIVE_PATH} could not be read safely: {registry.warning}",
        ),))
    matches = [r for r in registry.records if r.agent_id == agent_id]
    if len(matches) > 1:
        return _AgentUnassignLookup(None, (ConflictItem("duplicate-agent-id", f"{AGENT_REGISTRY_RELATIVE_PATH} has more than one record for '{agent_id}'"),))
    return _AgentUnassignLookup(matches[0] if matches else None, ())


def apply_task_unassign_agent(repo_root: Path, plan: TaskUnassignAgentPlan, clock: Clock | None = None) -> AgentOwnershipApplyOutcome:
    """Mirrors `apply_task_unassign`'s atomicity model. Never disables or
    deletes the agent, never changes task status, never touches Git or a
    worktree."""
    assert plan.task_record is not None

    fresh_plan = build_task_unassign_agent_plan(repo_root, plan.task_id)
    if fresh_plan.has_conflict:
        return AgentOwnershipApplyOutcome(
            ok=False, task_json_written=False, agent_registry_written=False,
            index_written=False, index_error=None,
            partial_state={
                "reason": "agent ownership state changed between preflight and apply - refusing to proceed",
                "conflicts": [{"key": c.key, "message": c.message} for c in fresh_plan.conflicts],
            },
        )

    now = iso_now(clock)
    task_dir = task_dir_for(repo_root, plan.task_id)
    original_task_record = plan.task_record
    updated_task_record = TaskRecord(**{**original_task_record.to_dict(), "agent_id": None, "updated_at": now})

    try:
        save_task_record(task_dir, updated_task_record)
    except OSError as exc:
        return AgentOwnershipApplyOutcome(
            ok=False, task_json_written=False, agent_registry_written=False,
            index_written=False, index_error=None,
            partial_state={"error": str(exc), "task_json_written": False},
        )

    agent_registry_written = False
    if plan.agent_record is not None:
        fresh_registry = load_agent_registry(repo_root)
        if fresh_registry.warning is not None:
            _rollback_task_record(task_dir, original_task_record)
            return AgentOwnershipApplyOutcome(
                ok=False, task_json_written=False, agent_registry_written=False,
                index_written=False, index_error=None,
                partial_state={"error": fresh_registry.warning, "task_json_written": True, "rolled_back": True},
            )
        updated_agent_records = [
            AgentRecord(**{**r.to_dict(), "assigned_task_id": None, "updated_at": now}) if r.agent_id == plan.agent_record.agent_id else r
            for r in fresh_registry.records
        ]
        try:
            save_agent_registry(repo_root, AgentRegistryDocument(
                schema_version=fresh_registry.schema_version, records=updated_agent_records,
            ))
            agent_registry_written = True
        except OSError as exc:
            _rollback_task_record(task_dir, original_task_record)
            return AgentOwnershipApplyOutcome(
                ok=False, task_json_written=False, agent_registry_written=False,
                index_written=False, index_error=None,
                partial_state={"error": str(exc), "task_json_written": True, "rolled_back": True},
            )

    index_written, index_error = _update_task_index_agent_ownership(repo_root, plan.task_id, None, now)
    return AgentOwnershipApplyOutcome(
        ok=True, task_json_written=True, agent_registry_written=agent_registry_written,
        index_written=index_written, index_error=index_error, partial_state=None,
    )


# --- shared helpers --------------------------------------------------------


def _rollback_task_record(task_dir: Path, original: TaskRecord) -> None:
    try:
        save_task_record(task_dir, original)
    except OSError:
        pass  # best-effort only; caller already reports partial_state


def _update_task_index_ownership(repo_root: Path, task_id: str, worktree_id: str | None, now: str) -> tuple[bool, str | None]:
    fresh_index = load_task_index(repo_root)
    if fresh_index.warning is not None:
        return False, fresh_index.warning
    updated_records = [
        TaskIndexRecord(**{**r.to_dict(), "worktree_id": worktree_id, "updated_at": now}) if r.task_id == task_id else r
        for r in fresh_index.records
    ]
    try:
        save_task_index(repo_root, TaskIndexDocument(
            schema_version=fresh_index.schema_version, next_task_number=fresh_index.next_task_number,
            records=updated_records,
        ))
        return True, None
    except OSError as exc:
        return False, str(exc)


def _update_task_index_agent_ownership(repo_root: Path, task_id: str, agent_id: str | None, now: str) -> tuple[bool, str | None]:
    fresh_index = load_task_index(repo_root)
    if fresh_index.warning is not None:
        return False, fresh_index.warning
    updated_records = [
        TaskIndexRecord(**{**r.to_dict(), "agent_id": agent_id, "updated_at": now}) if r.task_id == task_id else r
        for r in fresh_index.records
    ]
    try:
        save_task_index(repo_root, TaskIndexDocument(
            schema_version=fresh_index.schema_version, next_task_number=fresh_index.next_task_number,
            records=updated_records,
        ))
        return True, None
    except OSError as exc:
        return False, str(exc)
