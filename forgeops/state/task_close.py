"""Deterministic core logic behind `forgeops task close` (see
`forgeops/cli/task.py`): a read-only preflight-plan builder
(`build_task_close_plan`, reusing `forgeops.state.task_validate.validate_task`
for structural consistency) and the single mutating apply step
(`apply_task_close`). Confirmation-gated and, unlike creation, never
supports reopening - see docs/tasks.md "Status transitions"."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from forgeops.core.timestamps import Clock, iso_now
from forgeops.security.secret_scan import scan_text
from forgeops.state.atomic_write import atomic_write_text
from forgeops.state.task_registry import (
    CLOSEABLE_STATUSES,
    CLOSEABLE_VALIDATION_STATUSES,
    INDEX_RELATIVE_PATH,
    RESULT_MD_FILENAME,
    RESULT_STATE_RECORDED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    VALIDATION_STATUS_FAILED,
    VALIDATION_STATUS_WAIVED,
    TaskIndexDocument,
    TaskIndexRecord,
    TaskRecord,
    load_index,
    save_index,
    save_task_record,
)
from forgeops.state.task_spec import read_source_file
from forgeops.state.task_validate import TaskIssue, TaskValidationOutcome, validate_task

SEVERITY_BLOCKER = "blocker"


@dataclass(frozen=True)
class TaskClosePlan:
    repo_root: Path
    task_id: str
    validation: TaskValidationOutcome
    result_content: str | None
    planned_status: str | None
    conflicts: tuple[TaskIssue, ...] = field(default_factory=tuple)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


def build_task_close_plan(
    repo_root: Path,
    task_id: str,
    result_file: Path | None,
    max_result_bytes: int,
    git_available: bool,
    resolve_accepted_checkpoint,
) -> TaskClosePlan:
    """Read-only preflight shared by `--dry-run`, the missing-`--confirm`
    response, and a real run. Reuses `validate_task`'s full structural
    check so a task can never close while internally inconsistent, then
    adds closure-specific eligibility conflicts on top."""
    validation = validate_task(repo_root, task_id, git_available, resolve_accepted_checkpoint)
    conflicts: list[TaskIssue] = list(validation.blockers)

    result_content: str | None = None
    if result_file is None:
        conflicts.append(TaskIssue("missing-result-file", "--result-file is required to close a task", SEVERITY_BLOCKER))
    else:
        read = read_source_file(result_file, max_result_bytes)
        if not read.ok:
            conflicts.append(TaskIssue("result-file-invalid", f"--result-file: {read.error}", SEVERITY_BLOCKER))
        else:
            assert read.content is not None
            findings, _ = scan_text(read.content, relative_path=f".agent/tasks/{task_id}/__pending_result__.md")
            if findings:
                categories = ", ".join(sorted({f.category for f in findings}))
                conflicts.append(TaskIssue("result-file-secret-detected", f"--result-file appears to contain secret-shaped content ({categories}) - refusing to persist it", SEVERITY_BLOCKER))
            else:
                result_content = read.content

    planned_status: str | None = None
    if validation.task_record is not None:
        if validation.task_record.status not in CLOSEABLE_STATUSES:
            conflicts.append(TaskIssue("already-closed", f"task status is already '{validation.task_record.status}' - this checkpoint does not support reopening a task", SEVERITY_BLOCKER))

    if validation.validation_record is not None:
        vstatus = validation.validation_record.status
        if vstatus not in CLOSEABLE_VALIDATION_STATUSES:
            conflicts.append(TaskIssue(
                "validation-not-decided",
                f"VALIDATION.json status is '{vstatus}' - it must be one of {sorted(CLOSEABLE_VALIDATION_STATUSES)} before closing "
                "(this checkpoint never runs validation automatically - update VALIDATION.json first)",
                SEVERITY_BLOCKER,
            ))
        elif vstatus == VALIDATION_STATUS_WAIVED and not (validation.validation_record.approval_reference or "").strip():
            conflicts.append(TaskIssue("waived-without-approval-reference", "VALIDATION.json status is 'waived' but has no recorded approval_reference", SEVERITY_BLOCKER))
        else:
            planned_status = STATUS_FAILED if vstatus == VALIDATION_STATUS_FAILED else STATUS_COMPLETED
    elif validation.found and not any(i.key == "validation-json-malformed" for i in validation.issues):
        conflicts.append(TaskIssue("validation-not-decided", "VALIDATION.json could not be read", SEVERITY_BLOCKER))

    return TaskClosePlan(
        repo_root=repo_root, task_id=task_id, validation=validation,
        result_content=result_content, planned_status=planned_status, conflicts=tuple(conflicts),
    )


@dataclass(frozen=True)
class TaskCloseOutcome:
    ok: bool
    new_status: str | None
    result_written: bool
    task_json_written: bool
    index_written: bool
    index_error: str | None
    partial_state: dict | None


def apply_task_close(repo_root: Path, plan: TaskClosePlan, clock: Clock | None = None) -> TaskCloseOutcome:
    """Writes RESULT.md, then TASK.json, then TASK_INDEX.json - in that
    order, since RESULT.md is a brand-new file (cheapest to roll back)
    while TASK.json is the authoritative task record (rolling it back
    after a successful atomic write would itself be a second destructive
    replacement, which this checkpoint's instructions say not to risk).
    A failed TASK_INDEX.json update after a successful TASK.json+RESULT.md
    write is therefore reported as an incomplete-but-real closure
    (`ok=True`, `index_written=False`), mirroring how `worktree
    create`/`worktree remove` already treat a registry-write failure
    after their own real action succeeded."""
    assert plan.planned_status is not None and plan.result_content is not None
    assert plan.validation.task_dir is not None and plan.validation.task_record is not None

    task_dir = plan.validation.task_dir
    now = iso_now(clock)

    result_path = task_dir / RESULT_MD_FILENAME
    try:
        atomic_write_text(result_path, plan.result_content)
    except OSError as exc:
        return TaskCloseOutcome(
            ok=False, new_status=None, result_written=False, task_json_written=False,
            index_written=False, index_error=None,
            partial_state={"error": str(exc), "result_written": False},
        )

    updated_record = TaskRecord(
        schema_version=plan.validation.task_record.schema_version,
        task_id=plan.validation.task_record.task_id,
        title=plan.validation.task_record.title,
        status=plan.planned_status,
        project_root=plan.validation.task_record.project_root,
        created_at=plan.validation.task_record.created_at,
        updated_at=now,
        created_by=plan.validation.task_record.created_by,
        scope_summary=plan.validation.task_record.scope_summary,
        accepted_checkpoint=plan.validation.task_record.accepted_checkpoint,
        source_branch=plan.validation.task_record.source_branch,
        source_head=plan.validation.task_record.source_head,
        worktree_id=plan.validation.task_record.worktree_id,
        agent_id=plan.validation.task_record.agent_id,
        approval_state=plan.validation.task_record.approval_state,
        blockers=plan.validation.task_record.blockers,
        dependencies=plan.validation.task_record.dependencies,
        managed_files=plan.validation.task_record.managed_files,
        validation_state=plan.validation.validation_record.status,
        result_state=RESULT_STATE_RECORDED,
    )

    try:
        save_task_record(task_dir, updated_record)
    except OSError as exc:
        try:
            result_path.unlink(missing_ok=True)
        except OSError:
            pass
        return TaskCloseOutcome(
            ok=False, new_status=None, result_written=False, task_json_written=False,
            index_written=False, index_error=None,
            partial_state={"error": str(exc), "result_written": True, "task_json_written": False, "rolled_back_result": True},
        )

    index_written = False
    index_error: str | None = None
    fresh_index = load_index(repo_root)
    if fresh_index.warning is not None:
        index_error = f"{INDEX_RELATIVE_PATH} could not be read for update: {fresh_index.warning}"
    else:
        updated_records = [
            TaskIndexRecord(
                task_id=r.task_id, title=r.title,
                status=plan.planned_status if r.task_id == plan.task_id else r.status,
                created_at=r.created_at,
                updated_at=now if r.task_id == plan.task_id else r.updated_at,
                path=r.path, worktree_id=r.worktree_id, agent_id=r.agent_id,
                approval_state=r.approval_state,
                validation_state=updated_record.validation_state if r.task_id == plan.task_id else r.validation_state,
                result_state=RESULT_STATE_RECORDED if r.task_id == plan.task_id else r.result_state,
            )
            for r in fresh_index.records
        ]
        try:
            save_index(repo_root, TaskIndexDocument(
                schema_version=fresh_index.schema_version,
                next_task_number=fresh_index.next_task_number,
                records=updated_records,
            ))
            index_written = True
        except OSError as exc:
            index_error = str(exc)

    return TaskCloseOutcome(
        ok=True, new_status=plan.planned_status, result_written=True, task_json_written=True,
        index_written=index_written, index_error=index_error, partial_state=None,
    )
