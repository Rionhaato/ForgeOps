"""Schema, constants, and atomic load/save for the persistent Task
Specification Engine: `.agent/tasks/TASK_INDEX.json` (concise summaries,
mirrors `forgeops.state.worktree_registry`'s shape/safety properties)
plus the per-task `TASK.json` and `VALIDATION.json` documents living
under `.agent/tasks/<task-id>/`. See docs/tasks.md for the full
directory contract and lifecycle this codifies.

Like `worktree_registry.py`, any single unreadable index record fails
the *whole* index document closed rather than being skipped - task
creation and closure both need "no matching ID" to reliably mean "no
matching ID", not "one existed and was silently dropped". Read-only
commands (`task list`/`task show`) still report a malformed index as a
warning rather than refusing outright, matching `worktree list`'s
handling of a malformed worktree registry.

Never stores credentials, environment values, or raw command output -
managed task artifacts are scanned for secret-shaped content before
they are ever written (see `forgeops.state.task_create`/`task_close`)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forgeops.state.atomic_write import atomic_write_text

INDEX_SCHEMA_VERSION = 1
SUPPORTED_INDEX_SCHEMA_VERSIONS = {1}
TASK_SCHEMA_VERSION = 1
SUPPORTED_TASK_SCHEMA_VERSIONS = {1}
VALIDATION_SCHEMA_VERSION = 1
SUPPORTED_VALIDATION_SCHEMA_VERSIONS = {1}

TASKS_DIR_RELATIVE = Path(".agent") / "tasks"
INDEX_RELATIVE_PATH = TASKS_DIR_RELATIVE / "TASK_INDEX.json"

TASK_JSON_FILENAME = "TASK.json"
SPEC_MD_FILENAME = "SPEC.md"
VALIDATION_JSON_FILENAME = "VALIDATION.json"
RESULT_MD_FILENAME = "RESULT.md"

# A single safe path segment, always `task-` followed by at least four
# digits (`task-0001`, ..., `task-10000`, ...) - deliberately rejected
# rather than sanitized on any violation, the same allow-list philosophy
# `forgeops/worktrees/naming.py:validate_worktree_name` already
# established, so a traversal sequence, separator, or absolute path can
# never be mistaken for a valid task ID.
TASK_ID_RE = re.compile(r"^task-(\d{4,})$")
TASK_ID_MIN_DIGITS = 4

# Lifecycle statuses this checkpoint recognizes. Only `draft` (from
# `task create`) and `completed`/`failed` (from `task close`) are ever
# actually reached by this checkpoint's own commands - `ready`, `active`,
# `blocked`, `validation_pending`, and `cancelled` are reserved for later
# approval/agent-ownership checkpoints and are recognized here only so
# validation doesn't reject a task a later checkpoint has already moved
# forward.
STATUS_DRAFT = "draft"
STATUS_READY = "ready"
STATUS_ACTIVE = "active"
STATUS_BLOCKED = "blocked"
STATUS_VALIDATION_PENDING = "validation_pending"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

RECOGNIZED_STATUSES = frozenset({
    STATUS_DRAFT, STATUS_READY, STATUS_ACTIVE, STATUS_BLOCKED,
    STATUS_VALIDATION_PENDING, STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED,
})
TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED})
# Statuses `task close` is willing to transition away from - anything
# already terminal is refused (no reopening in this checkpoint).
CLOSEABLE_STATUSES = RECOGNIZED_STATUSES - TERMINAL_STATUSES

# `TASK.json.approval_state` - always written as `not_requested` by this
# checkpoint (no approvals checkpoint exists yet to produce another
# value); recognized as a single-member set now so `task validate`
# already has somewhere principled to check against once a future
# checkpoint adds more values, without a schema migration here.
APPROVAL_STATE_NOT_REQUESTED = "not_requested"
RECOGNIZED_APPROVAL_STATES = frozenset({APPROVAL_STATE_NOT_REQUESTED})

# `VALIDATION.json.status`.
VALIDATION_STATUS_NOT_RUN = "not_run"
VALIDATION_STATUS_PASSED = "passed"
VALIDATION_STATUS_FAILED = "failed"
VALIDATION_STATUS_WAIVED = "waived"
RECOGNIZED_VALIDATION_STATUSES = frozenset({
    VALIDATION_STATUS_NOT_RUN, VALIDATION_STATUS_PASSED,
    VALIDATION_STATUS_FAILED, VALIDATION_STATUS_WAIVED,
})
# The only validation statuses `task close` is ever willing to close
# against - `not_run` is deliberately excluded, matching the checkpoint's
# explicit instruction that it never executes validation automatically.
CLOSEABLE_VALIDATION_STATUSES = frozenset({
    VALIDATION_STATUS_PASSED, VALIDATION_STATUS_FAILED, VALIDATION_STATUS_WAIVED,
})

RESULT_STATE_PENDING = "pending"
RESULT_STATE_RECORDED = "recorded"


def task_dir_for(repo_root: Path, task_id: str) -> Path:
    return repo_root / TASKS_DIR_RELATIVE / task_id


def validate_task_id(task_id: str) -> str | None:
    """Return None if `task_id` is a valid, unambiguous task ID, or a
    human-readable rejection reason otherwise. Never mutates `task_id` -
    an invalid ID is rejected outright, never sanitized, so a rejected
    value never silently becomes a different, unintended one."""
    if not task_id:
        return "task ID must not be empty"
    if Path(task_id).is_absolute():
        return "task ID must not be an absolute path"
    if not TASK_ID_RE.match(task_id):
        return (
            f"task ID must match 'task-' followed by at least {TASK_ID_MIN_DIGITS} digits "
            "(e.g. 'task-0001') - rejected rather than sanitized, to avoid silently changing its meaning"
        )
    return None


# --- TASK_INDEX.json ---------------------------------------------------


@dataclass(frozen=True)
class TaskIndexRecord:
    task_id: str
    title: str
    status: str
    created_at: str
    updated_at: str
    path: str
    worktree_id: str | None = None
    agent_id: str | None = None
    approval_state: str = APPROVAL_STATE_NOT_REQUESTED
    validation_state: str = VALIDATION_STATUS_NOT_RUN
    result_state: str = RESULT_STATE_PENDING

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "path": self.path,
            "worktree_id": self.worktree_id,
            "agent_id": self.agent_id,
            "approval_state": self.approval_state,
            "validation_state": self.validation_state,
            "result_state": self.result_state,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "TaskIndexRecord":
        return TaskIndexRecord(
            task_id=str(data["task_id"]),
            title=str(data.get("title", "")),
            status=str(data.get("status", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            path=str(data.get("path", "")),
            worktree_id=data.get("worktree_id"),
            agent_id=data.get("agent_id"),
            approval_state=str(data.get("approval_state", APPROVAL_STATE_NOT_REQUESTED)),
            validation_state=str(data.get("validation_state", VALIDATION_STATUS_NOT_RUN)),
            result_state=str(data.get("result_state", RESULT_STATE_PENDING)),
        )


@dataclass(frozen=True)
class TaskIndexDocument:
    schema_version: int = INDEX_SCHEMA_VERSION
    next_task_number: int = 1
    records: list[TaskIndexRecord] = field(default_factory=list)
    warning: str | None = None

    def to_json(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "next_task_number": self.next_task_number,
            "records": [r.to_dict() for r in self.records],
        }
        return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def load_index(repo_root: Path) -> TaskIndexDocument:
    path = repo_root / INDEX_RELATIVE_PATH
    if not path.is_file():
        return TaskIndexDocument(records=[])

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return TaskIndexDocument(records=[], warning=f"could not read {path.name}: {exc}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return TaskIndexDocument(records=[], warning=f"{path.name} is not valid JSON: {exc}")

    if not isinstance(data, dict):
        return TaskIndexDocument(records=[], warning=f"{path.name} top-level value is not an object")

    version = data.get("schema_version")
    if version not in SUPPORTED_INDEX_SCHEMA_VERSIONS:
        return TaskIndexDocument(
            records=[],
            warning=(
                f"{path.name} has schema_version={version!r}, which this version of "
                f"forgeops does not know how to read (supported: "
                f"{sorted(SUPPORTED_INDEX_SCHEMA_VERSIONS)}); treated as malformed"
            ),
        )

    next_task_number = data.get("next_task_number")
    if not isinstance(next_task_number, int) or isinstance(next_task_number, bool) or next_task_number < 1:
        return TaskIndexDocument(records=[], warning=f"{path.name} 'next_task_number' is not a positive integer; treated as malformed")

    raw_records = data.get("records", [])
    if not isinstance(raw_records, list):
        return TaskIndexDocument(records=[], warning=f"{path.name} 'records' is not a list; treated as malformed")

    records: list[TaskIndexRecord] = []
    for entry in raw_records:
        if not isinstance(entry, dict) or "task_id" not in entry:
            return TaskIndexDocument(records=[], warning=f"{path.name} contains a record missing required field 'task_id'; treated as malformed")
        try:
            records.append(TaskIndexRecord.from_dict(entry))
        except (TypeError, ValueError) as exc:
            return TaskIndexDocument(records=[], warning=f"{path.name} contains an unreadable record: {exc}; treated as malformed")

    return TaskIndexDocument(schema_version=version, next_task_number=next_task_number, records=records)


def save_index(repo_root: Path, document: TaskIndexDocument) -> Path:
    path = repo_root / INDEX_RELATIVE_PATH
    atomic_write_text(path, document.to_json())
    return path


# --- TASK.json -----------------------------------------------------------


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    title: str
    status: str
    project_root: str
    created_at: str
    updated_at: str
    created_by: str
    scope_summary: str
    accepted_checkpoint: str | None
    source_branch: str | None
    source_head: str | None
    schema_version: int = TASK_SCHEMA_VERSION
    worktree_id: str | None = None
    agent_id: str | None = None
    approval_state: str = APPROVAL_STATE_NOT_REQUESTED
    blockers: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    managed_files: list[str] = field(default_factory=list)
    validation_state: str = VALIDATION_STATUS_NOT_RUN
    result_state: str = RESULT_STATE_PENDING

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status,
            "project_root": self.project_root,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "scope_summary": self.scope_summary,
            "accepted_checkpoint": self.accepted_checkpoint,
            "source_branch": self.source_branch,
            "source_head": self.source_head,
            "worktree_id": self.worktree_id,
            "agent_id": self.agent_id,
            "approval_state": self.approval_state,
            "blockers": list(self.blockers),
            "dependencies": list(self.dependencies),
            "managed_files": list(self.managed_files),
            "validation_state": self.validation_state,
            "result_state": self.result_state,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "TaskRecord":
        return TaskRecord(
            schema_version=int(data.get("schema_version", TASK_SCHEMA_VERSION)),
            task_id=str(data["task_id"]),
            title=str(data.get("title", "")),
            status=str(data.get("status", "")),
            project_root=str(data.get("project_root", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            created_by=str(data.get("created_by", "")),
            scope_summary=str(data.get("scope_summary", "")),
            accepted_checkpoint=data.get("accepted_checkpoint"),
            source_branch=data.get("source_branch"),
            source_head=data.get("source_head"),
            worktree_id=data.get("worktree_id"),
            agent_id=data.get("agent_id"),
            approval_state=str(data.get("approval_state", APPROVAL_STATE_NOT_REQUESTED)),
            blockers=list(data.get("blockers") or []),
            dependencies=list(data.get("dependencies") or []),
            managed_files=list(data.get("managed_files") or []),
            validation_state=str(data.get("validation_state", VALIDATION_STATUS_NOT_RUN)),
            result_state=str(data.get("result_state", RESULT_STATE_PENDING)),
        )


@dataclass(frozen=True)
class TaskRecordLoad:
    record: TaskRecord | None
    warning: str | None


def load_task_record(task_dir: Path) -> TaskRecordLoad:
    path = task_dir / TASK_JSON_FILENAME
    if not path.is_file():
        return TaskRecordLoad(record=None, warning=f"{TASK_JSON_FILENAME} does not exist")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return TaskRecordLoad(record=None, warning=f"could not read {TASK_JSON_FILENAME}: {exc}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return TaskRecordLoad(record=None, warning=f"{TASK_JSON_FILENAME} is not valid JSON: {exc}")
    if not isinstance(data, dict) or "task_id" not in data:
        return TaskRecordLoad(record=None, warning=f"{TASK_JSON_FILENAME} is missing required field 'task_id'")
    try:
        return TaskRecordLoad(record=TaskRecord.from_dict(data), warning=None)
    except (TypeError, ValueError) as exc:
        return TaskRecordLoad(record=None, warning=f"{TASK_JSON_FILENAME} is unreadable: {exc}")


def save_task_record(task_dir: Path, record: TaskRecord) -> Path:
    path = task_dir / TASK_JSON_FILENAME
    atomic_write_text(path, json.dumps(record.to_dict(), indent=2, sort_keys=False) + "\n")
    return path


# --- VALIDATION.json -------------------------------------------------------


@dataclass(frozen=True)
class ValidationRecord:
    task_id: str
    updated_at: str
    schema_version: int = VALIDATION_SCHEMA_VERSION
    status: str = VALIDATION_STATUS_NOT_RUN
    required_checks: list[str] = field(default_factory=list)
    observed_checks: list[str] = field(default_factory=list)
    test_summary: str | None = None
    compile_summary: str | None = None
    diff_check_summary: str | None = None
    manual_checks: list[str] = field(default_factory=list)
    evidence_references: list[str] = field(default_factory=list)
    approval_reference: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "status": self.status,
            "required_checks": list(self.required_checks),
            "observed_checks": list(self.observed_checks),
            "test_summary": self.test_summary,
            "compile_summary": self.compile_summary,
            "diff_check_summary": self.diff_check_summary,
            "manual_checks": list(self.manual_checks),
            "evidence_references": list(self.evidence_references),
            "approval_reference": self.approval_reference,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "ValidationRecord":
        return ValidationRecord(
            schema_version=int(data.get("schema_version", VALIDATION_SCHEMA_VERSION)),
            task_id=str(data["task_id"]),
            status=str(data.get("status", VALIDATION_STATUS_NOT_RUN)),
            required_checks=list(data.get("required_checks") or []),
            observed_checks=list(data.get("observed_checks") or []),
            test_summary=data.get("test_summary"),
            compile_summary=data.get("compile_summary"),
            diff_check_summary=data.get("diff_check_summary"),
            manual_checks=list(data.get("manual_checks") or []),
            evidence_references=list(data.get("evidence_references") or []),
            approval_reference=data.get("approval_reference"),
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass(frozen=True)
class ValidationRecordLoad:
    record: ValidationRecord | None
    warning: str | None


def load_validation_record(task_dir: Path) -> ValidationRecordLoad:
    path = task_dir / VALIDATION_JSON_FILENAME
    if not path.is_file():
        return ValidationRecordLoad(record=None, warning=f"{VALIDATION_JSON_FILENAME} does not exist")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ValidationRecordLoad(record=None, warning=f"could not read {VALIDATION_JSON_FILENAME}: {exc}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ValidationRecordLoad(record=None, warning=f"{VALIDATION_JSON_FILENAME} is not valid JSON: {exc}")
    if not isinstance(data, dict) or "task_id" not in data:
        return ValidationRecordLoad(record=None, warning=f"{VALIDATION_JSON_FILENAME} is missing required field 'task_id'")
    try:
        return ValidationRecordLoad(record=ValidationRecord.from_dict(data), warning=None)
    except (TypeError, ValueError) as exc:
        return ValidationRecordLoad(record=None, warning=f"{VALIDATION_JSON_FILENAME} is unreadable: {exc}")


def save_validation_record(task_dir: Path, record: ValidationRecord) -> Path:
    path = task_dir / VALIDATION_JSON_FILENAME
    atomic_write_text(path, json.dumps(record.to_dict(), indent=2, sort_keys=False) + "\n")
    return path


def default_validation_record(task_id: str, updated_at: str) -> ValidationRecord:
    return ValidationRecord(task_id=task_id, updated_at=updated_at, status=VALIDATION_STATUS_NOT_RUN)
