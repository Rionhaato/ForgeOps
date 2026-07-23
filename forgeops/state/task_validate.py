"""Read-only structural/consistency validation for a single task's
managed artifacts - the core logic behind `forgeops task validate`, and
reused by `forgeops task close`'s own preflight (a task must already be
internally consistent before it can close). See docs/tasks.md for the
full check list.

`validate_task` never writes anything, never runs project tests or
validation commands, never assigns a worktree/agent, and never changes
status - it only reads TASK_INDEX.json and the target task's own
directory and reports what it finds, split into blocking issues and
non-blocking warnings."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from forgeops.core.paths import is_protected_reference_path
from forgeops.security.secret_scan import scan_text
from forgeops.state.task_registry import (
    INDEX_RELATIVE_PATH,
    RECOGNIZED_APPROVAL_STATES,
    RECOGNIZED_STATUSES,
    RECOGNIZED_VALIDATION_STATUSES,
    TASKS_DIR_RELATIVE,
    TERMINAL_STATUSES,
    TaskRecord,
    ValidationRecord,
    load_index,
    load_task_record,
    load_validation_record,
    task_dir_for,
    validate_task_id,
)
from forgeops.state.task_spec import (
    REQUIRED_SPEC_HEADINGS,
    is_placeholder_section,
    missing_required_headings,
    parse_markdown_sections,
)
from forgeops.state.worktree_registry import (
    REGISTRY_RELATIVE_PATH as WORKTREE_REGISTRY_RELATIVE_PATH,
    STATUS_ACTIVE as WORKTREE_STATUS_ACTIVE,
    STATUS_REMOVED as WORKTREE_STATUS_REMOVED,
    load_registry as load_worktree_registry,
)
from forgeops.worktrees.naming import validate_worktree_name

SEVERITY_BLOCKER = "blocker"
SEVERITY_WARNING = "warning"


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


@dataclass(frozen=True)
class TaskIssue:
    key: str
    message: str
    severity: str


@dataclass(frozen=True)
class TaskValidationOutcome:
    task_id: str
    found: bool
    task_dir: Path | None
    task_record: TaskRecord | None
    validation_record: ValidationRecord | None
    spec_text: str | None
    result_text: str | None
    in_index: bool
    issues: tuple[TaskIssue, ...] = field(default_factory=tuple)

    @property
    def blockers(self) -> list[TaskIssue]:
        return [i for i in self.issues if i.severity == SEVERITY_BLOCKER]

    @property
    def warnings(self) -> list[TaskIssue]:
        return [i for i in self.issues if i.severity == SEVERITY_WARNING]

    @property
    def has_blockers(self) -> bool:
        return bool(self.blockers)


def validate_task(
    repo_root: Path,
    task_id: str,
    git_available: bool,
    resolve_accepted_checkpoint,
) -> TaskValidationOutcome:
    """`resolve_accepted_checkpoint` is a callable `(repo_root, ref) ->
    str | None` (normally `forgeops.worktrees.git_worktree.resolve_commit`)
    - injected rather than imported directly so tests can substitute a
    fake without needing a real git history, and so this module has no
    hard dependency on the git executable being present. Only called at
    all when `git_available` is True - "accepted checkpoint exists when
    Git is available" is entirely skipped (not even a warning) when it
    isn't, per this checkpoint's explicit instruction."""
    issues: list[TaskIssue] = []

    id_error = validate_task_id(task_id)
    if id_error is not None:
        issues.append(TaskIssue("invalid-task-id", id_error, SEVERITY_BLOCKER))
        return TaskValidationOutcome(
            task_id=task_id, found=False, task_dir=None, task_record=None, validation_record=None,
            spec_text=None, result_text=None, in_index=False, issues=tuple(issues),
        )

    task_dir = task_dir_for(repo_root, task_id)
    try:
        task_dir.resolve().relative_to((repo_root / TASKS_DIR_RELATIVE).resolve())
        path_ok = True
    except ValueError:
        path_ok = False
        issues.append(TaskIssue("path-escape", f"{task_dir} is not beneath the managed tasks root", SEVERITY_BLOCKER))

    if is_protected_reference_path(task_dir):
        issues.append(TaskIssue(
            "protected-reference-repo",
            f"{task_dir} is (or is beneath) the configured read-only reference repository",
            SEVERITY_BLOCKER,
        ))

    index = load_index(repo_root)
    if index.warning is not None:
        issues.append(TaskIssue("index-malformed", f"{INDEX_RELATIVE_PATH} could not be read safely: {index.warning}", SEVERITY_BLOCKER))
        index_records = []
    else:
        index_records = index.records

    matches = [r for r in index_records if r.task_id == task_id]
    duplicate_names = [r.task_id for r in index_records]
    if duplicate_names.count(task_id) > 1:
        issues.append(TaskIssue("duplicate-task-id", f"{INDEX_RELATIVE_PATH} contains more than one record for '{task_id}'", SEVERITY_BLOCKER))
    in_index = bool(matches)
    index_record = matches[0] if matches else None

    if not path_ok or not task_dir.is_dir():
        if not in_index:
            issues.append(TaskIssue("not-found", f"no task '{task_id}' found (no directory and no index entry)", SEVERITY_BLOCKER))
        else:
            issues.append(TaskIssue("index-directory-mismatch", f"'{task_id}' is present in {INDEX_RELATIVE_PATH} but its directory does not exist", SEVERITY_BLOCKER))
        return TaskValidationOutcome(
            task_id=task_id, found=in_index, task_dir=task_dir if path_ok else None, task_record=None,
            validation_record=None, spec_text=None, result_text=None, in_index=in_index, issues=tuple(issues),
        )

    if not in_index:
        issues.append(TaskIssue("index-directory-mismatch", f"'{task_id}' has a directory on disk but no entry in {INDEX_RELATIVE_PATH} (unindexed)", SEVERITY_WARNING))
    elif _norm(index_record.path) != _norm(Path(".agent") / "tasks" / task_id):
        issues.append(TaskIssue("index-path-mismatch", f"index path '{index_record.path}' does not match the deterministic path for '{task_id}'", SEVERITY_BLOCKER))

    task_load = load_task_record(task_dir)
    task_record = task_load.record
    if task_load.warning is not None:
        issues.append(TaskIssue("task-json-malformed", f"TASK.json: {task_load.warning}", SEVERITY_BLOCKER))

    spec_path = task_dir / "SPEC.md"
    spec_text: str | None = None
    if not spec_path.is_file():
        issues.append(TaskIssue("spec-missing", "SPEC.md does not exist", SEVERITY_BLOCKER))
    else:
        try:
            spec_text = spec_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(TaskIssue("spec-unreadable", f"SPEC.md could not be read: {exc}", SEVERITY_BLOCKER))

    if spec_text is not None:
        missing_headings = missing_required_headings(spec_text, REQUIRED_SPEC_HEADINGS)
        if missing_headings:
            issues.append(TaskIssue("spec-missing-headings", f"SPEC.md is missing required section(s): {', '.join(missing_headings)}", SEVERITY_BLOCKER))
        else:
            sections = parse_markdown_sections(spec_text, REQUIRED_SPEC_HEADINGS)
            if is_placeholder_section(sections.get("Acceptance Criteria", "")):
                issues.append(TaskIssue("acceptance-criteria-missing", "SPEC.md 'Acceptance Criteria' section is still a placeholder - no criteria defined yet", SEVERITY_BLOCKER))
            if is_placeholder_section(sections.get("Required Validation", "")):
                issues.append(TaskIssue("required-validation-missing", "SPEC.md 'Required Validation' section is still a placeholder - no validation requirements defined yet", SEVERITY_BLOCKER))

        secret_findings, _ = scan_text(spec_text, relative_path=f".agent/tasks/{task_id}/SPEC.md")
        if secret_findings:
            categories = ", ".join(sorted({f.category for f in secret_findings}))
            issues.append(TaskIssue("spec-secret-content", f"SPEC.md appears to contain secret-shaped content ({categories})", SEVERITY_BLOCKER))

    if task_record is not None:
        if task_record.task_id != task_id:
            issues.append(TaskIssue("task-id-mismatch", f"TASK.json task_id '{task_record.task_id}' does not match requested '{task_id}'", SEVERITY_BLOCKER))
        if _norm(task_record.project_root) != _norm(repo_root):
            issues.append(TaskIssue("project-root-mismatch", f"TASK.json project_root '{task_record.project_root}' does not match this project ({repo_root})", SEVERITY_BLOCKER))
        if task_record.status not in RECOGNIZED_STATUSES:
            issues.append(TaskIssue("invalid-status", f"TASK.json status '{task_record.status}' is not a recognized status", SEVERITY_BLOCKER))
        if task_record.approval_state not in RECOGNIZED_APPROVAL_STATES:
            issues.append(TaskIssue("invalid-approval-state", f"TASK.json approval_state '{task_record.approval_state}' is not recognized", SEVERITY_BLOCKER))
        if task_record.worktree_id is not None and not isinstance(task_record.worktree_id, str):
            issues.append(TaskIssue("invalid-worktree-field", "TASK.json worktree_id must be null or a string", SEVERITY_BLOCKER))
        else:
            issues.extend(_check_ownership_consistency(repo_root, task_id, task_record))
        if task_record.agent_id is not None and not isinstance(task_record.agent_id, str):
            issues.append(TaskIssue("invalid-agent-field", "TASK.json agent_id must be null or a string", SEVERITY_BLOCKER))

        if git_available and task_record.source_head:
            resolved = resolve_accepted_checkpoint(repo_root, task_record.accepted_checkpoint or task_record.source_head)
            if resolved is None:
                issues.append(TaskIssue(
                    "accepted-checkpoint-unresolvable",
                    "accepted_checkpoint/source_head could not be resolved in this repository's git history "
                    "(history may have changed since the task was created)",
                    SEVERITY_WARNING,
                ))

        task_json_text = json.dumps(task_record.to_dict())
        secret_findings, _ = scan_text(task_json_text, relative_path=f".agent/tasks/{task_id}/TASK.json")
        if secret_findings:
            categories = ", ".join(sorted({f.category for f in secret_findings}))
            issues.append(TaskIssue("task-json-secret-content", f"TASK.json appears to contain secret-shaped content ({categories})", SEVERITY_BLOCKER))

    validation_load = load_validation_record(task_dir)
    validation_record = validation_load.record
    if validation_load.warning is not None:
        issues.append(TaskIssue("validation-json-malformed", f"VALIDATION.json: {validation_load.warning}", SEVERITY_BLOCKER))
    elif validation_record is not None:
        if validation_record.status not in RECOGNIZED_VALIDATION_STATUSES:
            issues.append(TaskIssue("invalid-validation-status", f"VALIDATION.json status '{validation_record.status}' is not recognized", SEVERITY_BLOCKER))
        validation_json_text = json.dumps(validation_record.to_dict())
        secret_findings, _ = scan_text(validation_json_text, relative_path=f".agent/tasks/{task_id}/VALIDATION.json")
        if secret_findings:
            categories = ", ".join(sorted({f.category for f in secret_findings}))
            issues.append(TaskIssue("validation-json-secret-content", f"VALIDATION.json appears to contain secret-shaped content ({categories})", SEVERITY_BLOCKER))

    result_path = task_dir / "RESULT.md"
    result_text: str | None = None
    result_exists = result_path.is_file()
    if result_exists:
        try:
            result_text = result_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(TaskIssue("result-unreadable", f"RESULT.md could not be read: {exc}", SEVERITY_BLOCKER))

    if task_record is not None:
        is_terminal = task_record.status in TERMINAL_STATUSES
        if is_terminal and not result_exists:
            issues.append(TaskIssue("result-missing-for-terminal-status", f"task status is '{task_record.status}' but RESULT.md does not exist", SEVERITY_WARNING))
        elif not is_terminal and result_exists:
            issues.append(TaskIssue("result-present-for-non-terminal-status", f"RESULT.md exists but task status is '{task_record.status}' (not yet closed)", SEVERITY_WARNING))

    if result_text is not None:
        secret_findings, _ = scan_text(result_text, relative_path=f".agent/tasks/{task_id}/RESULT.md")
        if secret_findings:
            categories = ", ".join(sorted({f.category for f in secret_findings}))
            issues.append(TaskIssue("result-secret-content", f"RESULT.md appears to contain secret-shaped content ({categories})", SEVERITY_BLOCKER))

    return TaskValidationOutcome(
        task_id=task_id, found=True, task_dir=task_dir, task_record=task_record,
        validation_record=validation_record, spec_text=spec_text, result_text=result_text,
        in_index=in_index, issues=tuple(issues),
    )


def _check_ownership_consistency(repo_root: Path, task_id: str, task_record: TaskRecord) -> list[TaskIssue]:
    """Cross-checks `TASK.json.worktree_id` against
    `WORKTREE_REGISTRY.json` in both directions - see
    `forgeops/state/task_ownership.py` for how `task assign`/`task
    unassign` keep the two in sync, and docs/tasks.md "Ownership" for
    the one-to-one model this verifies. Read-only; never repairs
    anything it finds inconsistent."""
    issues: list[TaskIssue] = []

    registry = load_worktree_registry(repo_root)
    if registry.warning is not None:
        issues.append(TaskIssue(
            "worktree-registry-malformed",
            f"{WORKTREE_REGISTRY_RELATIVE_PATH} could not be read safely: {registry.warning}",
            SEVERITY_BLOCKER,
        ))
        return issues

    worktree_id = task_record.worktree_id
    if worktree_id is not None:
        name_error = validate_worktree_name(worktree_id)
        if name_error is not None:
            issues.append(TaskIssue(
                "worktree-id-schema-mismatch",
                f"TASK.json worktree_id '{worktree_id}' is not a validly-formed worktree name: {name_error}",
                SEVERITY_BLOCKER,
            ))
            worktree_id = None  # not safely usable for the lookups below
        else:
            matches = [r for r in registry.records if r.name == worktree_id]
            if not matches:
                issues.append(TaskIssue(
                    "worktree-missing",
                    f"task references worktree '{worktree_id}' which is not registered in {WORKTREE_REGISTRY_RELATIVE_PATH}",
                    SEVERITY_BLOCKER,
                ))
            else:
                record = matches[0]
                if record.status == WORKTREE_STATUS_REMOVED:
                    issues.append(TaskIssue(
                        "worktree-removed",
                        f"task references worktree '{worktree_id}' which has been removed",
                        SEVERITY_BLOCKER,
                    ))
                elif record.task_id is None:
                    issues.append(TaskIssue(
                        "orphan-task-ownership",
                        f"task claims worktree '{worktree_id}' but the worktree registry does not reciprocally claim this task",
                        SEVERITY_BLOCKER,
                    ))
                elif record.task_id != task_id:
                    issues.append(TaskIssue(
                        "ownership-mismatch",
                        f"task claims worktree '{worktree_id}' but the worktree registry says it belongs to task '{record.task_id}'",
                        SEVERITY_BLOCKER,
                    ))

    # Bidirectional: does any active worktree claim this task without
    # the task reciprocating (or reciprocating a different worktree)?
    claiming = [r for r in registry.records if r.status == WORKTREE_STATUS_ACTIVE and r.task_id == task_id]
    if len(claiming) > 1:
        issues.append(TaskIssue(
            "duplicate-assignment",
            f"more than one active worktree claims task '{task_id}': {', '.join(sorted(r.name for r in claiming))}",
            SEVERITY_BLOCKER,
        ))
    elif len(claiming) == 1 and worktree_id is None:
        issues.append(TaskIssue(
            "orphan-registry-ownership",
            f"worktree '{claiming[0].name}' claims task '{task_id}' but the task's own worktree_id is null",
            SEVERITY_BLOCKER,
        ))
    elif len(claiming) == 1 and worktree_id != claiming[0].name:
        issues.append(TaskIssue(
            "duplicate-assignment",
            f"worktree '{claiming[0].name}' claims task '{task_id}' while the task itself is assigned to a different worktree ('{worktree_id}')",
            SEVERITY_BLOCKER,
        ))

    return issues
