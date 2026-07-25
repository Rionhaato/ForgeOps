"""Deterministic core logic behind `forgeops task request-approval|approve|
reject|cancel-approval` (see `forgeops/cli/task.py`): read-only preflight-
plan builders and a single mutating apply step for each, following the
same plan/apply split every other mutating ForgeOps command uses.

Persistent human approval state for an *existing* task only - this
module never executes a task, launches an agent, creates a session, or
changes `TASK.json.status`/`worktree_id`/`agent_id`. Approval state and
its append-only `approval_history` are the only fields it ever writes,
stored entirely within `TASK.json` (`forgeops/state/task_registry.py`);
`TASK_INDEX.json`'s own `approval_state` summary field is kept in sync
as part of the same atomic operation. Unlike `task assign`/`task
assign-agent` (where `TASK_INDEX.json` is bookkeeping-only and its
own write failure is a warning), an approval mutation treats
`TASK.json` + `TASK_INDEX.json` as an atomic pair: if the index write
fails after `TASK.json` already succeeded, `TASK.json` is rolled back
and the whole call reports failure via `partial_state`, never a
"succeeded with a warning" result - approval state must never appear
authoritative in one file and stale in the other, even transiently."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from forgeops.core.timestamps import Clock, iso_now
from forgeops.security.secret_scan import scan_text
from forgeops.state.task_ownership import _lookup_task, _rollback_task_record
from forgeops.state.task_registry import (
    APPROVAL_ACTION_APPROVED,
    APPROVAL_ACTION_CANCELLED,
    APPROVAL_ACTION_REJECTED,
    APPROVAL_ACTION_REQUESTED,
    APPROVAL_TRANSITIONS,
    INDEX_RELATIVE_PATH,
    MAX_APPROVAL_REASON_LENGTH,
    RECOGNIZED_APPROVAL_STATES,
    TERMINAL_STATUSES,
    ApprovalEvent,
    TaskIndexDocument,
    TaskIndexRecord,
    TaskRecord,
    load_index as load_task_index,
    save_index as save_task_index,
    save_task_record,
    task_dir_for,
    validate_actor,
)
from forgeops.state.worktree_create import ConflictItem


@dataclass(frozen=True)
class TaskApprovalPlan:
    repo_root: Path
    task_id: str
    action: str
    actor: str
    reason: str | None
    task_record: TaskRecord | None
    current_approval_state: str | None
    planned_approval_state: str | None
    conflicts: tuple[ConflictItem, ...] = field(default_factory=tuple)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


def _build_approval_plan(
    repo_root: Path, task_id: str, action: str, actor: str, reason: str | None, *, reason_required: bool,
) -> TaskApprovalPlan:
    """Read-only preflight, shared by `--dry-run` and a real run, and
    re-run verbatim by `_apply_approval_transition` immediately before
    mutating to close the preflight/apply TOCTOU gap. Never writes
    anything."""
    conflicts: list[ConflictItem] = []

    task_lookup = _lookup_task(repo_root, task_id)
    conflicts.extend(task_lookup.conflicts)

    actor_error = validate_actor(actor)
    if actor_error is not None:
        conflicts.append(ConflictItem("invalid-actor", actor_error))
    else:
        findings, _ = scan_text(actor, relative_path=f".agent/tasks/{task_id}/__actor__")
        if findings:
            categories = ", ".join(sorted({f.category for f in findings}))
            conflicts.append(ConflictItem(
                "actor-secret-detected",
                f"actor appears to contain secret-shaped content ({categories}) - refusing to persist it",
            ))

    normalized_reason = reason or ""
    if len(normalized_reason) > MAX_APPROVAL_REASON_LENGTH:
        conflicts.append(ConflictItem(
            "invalid-reason", f"reason must be at most {MAX_APPROVAL_REASON_LENGTH} characters",
        ))
    elif normalized_reason:
        findings, _ = scan_text(normalized_reason, relative_path=f".agent/tasks/{task_id}/__approval_reason__")
        if findings:
            categories = ", ".join(sorted({f.category for f in findings}))
            conflicts.append(ConflictItem(
                "reason-secret-detected",
                f"reason appears to contain secret-shaped content ({categories}) - refusing to persist it",
            ))

    if reason_required and not normalized_reason.strip():
        conflicts.append(ConflictItem("reason-required", f"a reason is required for a '{action}' approval event"))

    current_state: str | None = None
    planned_state: str | None = None
    record = task_lookup.record
    if record is not None:
        if record.status in TERMINAL_STATUSES:
            conflicts.append(ConflictItem(
                "task-already-terminal",
                f"task status is '{record.status}' - a terminal task's approval state cannot be changed",
            ))

        current_state = record.approval_state
        if current_state not in RECOGNIZED_APPROVAL_STATES:
            conflicts.append(ConflictItem(
                "invalid-approval-state", f"TASK.json approval_state '{current_state}' is not recognized",
            ))
        else:
            planned_state = APPROVAL_TRANSITIONS.get((current_state, action))
            if planned_state is None:
                conflicts.append(ConflictItem(
                    "invalid-approval-transition",
                    f"action '{action}' is not a valid transition from approval state '{current_state}'",
                ))

        index = load_task_index(repo_root)
        if index.warning is not None:
            conflicts.append(ConflictItem(
                "task-index-malformed", f"{INDEX_RELATIVE_PATH} could not be read safely: {index.warning}",
            ))
        else:
            idx_matches = [r for r in index.records if r.task_id == task_id]
            if idx_matches and idx_matches[0].approval_state != record.approval_state:
                conflicts.append(ConflictItem(
                    "task-index-approval-mismatch",
                    f"{INDEX_RELATIVE_PATH} approval_state does not match TASK.json for '{task_id}'",
                ))

    return TaskApprovalPlan(
        repo_root=repo_root, task_id=task_id, action=action, actor=actor, reason=reason,
        task_record=record, current_approval_state=current_state, planned_approval_state=planned_state,
        conflicts=tuple(conflicts),
    )


def build_task_request_approval_plan(repo_root: Path, task_id: str, actor: str, reason: str | None = None) -> TaskApprovalPlan:
    return _build_approval_plan(repo_root, task_id, APPROVAL_ACTION_REQUESTED, actor, reason, reason_required=False)


def build_task_approve_plan(repo_root: Path, task_id: str, actor: str, reason: str | None = None) -> TaskApprovalPlan:
    return _build_approval_plan(repo_root, task_id, APPROVAL_ACTION_APPROVED, actor, reason, reason_required=False)


def build_task_reject_plan(repo_root: Path, task_id: str, actor: str, reason: str | None = None) -> TaskApprovalPlan:
    return _build_approval_plan(repo_root, task_id, APPROVAL_ACTION_REJECTED, actor, reason, reason_required=True)


def build_task_cancel_approval_plan(repo_root: Path, task_id: str, actor: str, reason: str | None = None) -> TaskApprovalPlan:
    return _build_approval_plan(repo_root, task_id, APPROVAL_ACTION_CANCELLED, actor, reason, reason_required=False)


@dataclass(frozen=True)
class TaskApprovalOutcome:
    ok: bool
    task_json_written: bool
    index_written: bool
    new_approval_state: str | None
    partial_state: dict | None


def _apply_approval_transition(
    repo_root: Path, plan: TaskApprovalPlan, clock: Clock | None, *, reason_required: bool,
) -> TaskApprovalOutcome:
    """`TASK.json` and `TASK_INDEX.json` are updated as an atomic pair
    for approval mutations (see module docstring) - if the index write
    fails after `TASK.json` already succeeded, `TASK.json` is rolled back
    (best-effort) and the whole call reports failure, never a partial
    approval state."""
    assert plan.task_record is not None and plan.planned_approval_state is not None

    fresh_plan = _build_approval_plan(
        repo_root, plan.task_id, plan.action, plan.actor, plan.reason, reason_required=reason_required,
    )
    if fresh_plan.has_conflict:
        return TaskApprovalOutcome(
            ok=False, task_json_written=False, index_written=False, new_approval_state=None,
            partial_state={
                "reason": "approval state changed between preflight and apply - refusing to proceed",
                "conflicts": [{"key": c.key, "message": c.message} for c in fresh_plan.conflicts],
            },
        )

    now = iso_now(clock)
    task_dir = task_dir_for(repo_root, plan.task_id)
    original_task_record = plan.task_record

    new_event = ApprovalEvent(action=plan.action, actor=plan.actor, timestamp=now, reason=plan.reason or "", reference=None)
    updated_history = [*original_task_record.approval_history, new_event]
    updated_task_record = TaskRecord(**{
        **original_task_record.to_dict(),
        "approval_state": plan.planned_approval_state,
        "approval_history": updated_history,
        "updated_at": now,
    })

    try:
        save_task_record(task_dir, updated_task_record)
    except OSError as exc:
        return TaskApprovalOutcome(
            ok=False, task_json_written=False, index_written=False, new_approval_state=None,
            partial_state={"error": str(exc), "task_json_written": False},
        )

    fresh_index = load_task_index(repo_root)
    if fresh_index.warning is not None:
        _rollback_task_record(task_dir, original_task_record)
        return TaskApprovalOutcome(
            ok=False, task_json_written=False, index_written=False, new_approval_state=None,
            partial_state={"error": fresh_index.warning, "task_json_written": True, "rolled_back": True},
        )

    updated_index_records = [
        TaskIndexRecord(**{**r.to_dict(), "approval_state": plan.planned_approval_state, "updated_at": now})
        if r.task_id == plan.task_id else r
        for r in fresh_index.records
    ]
    try:
        save_task_index(repo_root, TaskIndexDocument(
            schema_version=fresh_index.schema_version, next_task_number=fresh_index.next_task_number,
            records=updated_index_records,
        ))
    except OSError as exc:
        _rollback_task_record(task_dir, original_task_record)
        return TaskApprovalOutcome(
            ok=False, task_json_written=False, index_written=False, new_approval_state=None,
            partial_state={"error": str(exc), "task_json_written": True, "rolled_back": True},
        )

    return TaskApprovalOutcome(
        ok=True, task_json_written=True, index_written=True,
        new_approval_state=plan.planned_approval_state, partial_state=None,
    )


def apply_task_request_approval(repo_root: Path, plan: TaskApprovalPlan, clock: Clock | None = None) -> TaskApprovalOutcome:
    return _apply_approval_transition(repo_root, plan, clock, reason_required=False)


def apply_task_approve(repo_root: Path, plan: TaskApprovalPlan, clock: Clock | None = None) -> TaskApprovalOutcome:
    return _apply_approval_transition(repo_root, plan, clock, reason_required=False)


def apply_task_reject(repo_root: Path, plan: TaskApprovalPlan, clock: Clock | None = None) -> TaskApprovalOutcome:
    return _apply_approval_transition(repo_root, plan, clock, reason_required=True)


def apply_task_cancel_approval(repo_root: Path, plan: TaskApprovalPlan, clock: Clock | None = None) -> TaskApprovalOutcome:
    return _apply_approval_transition(repo_root, plan, clock, reason_required=False)
