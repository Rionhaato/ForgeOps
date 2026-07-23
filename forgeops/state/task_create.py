"""Deterministic core logic behind `forgeops task create` (see
`forgeops/cli/task.py`): a read-only preflight-plan builder
(`build_task_create_plan`) and the single mutating apply step
(`apply_task_create`), following the same split
`forgeops.state.worktree_create` uses for `forgeops worktree create`.

`build_task_create_plan` only ever reads (project-initialization check,
index lookup, source-file reads via `forgeops.state.task_spec`, secret
scanning) - it never writes anything, so the identical function backs
both `--dry-run` and a real run. `apply_task_create` is the only
function that mutates, and is only ever called after preflight found
zero conflicts: it writes the new task directory (TASK.json, SPEC.md,
VALIDATION.json) first, then - only after that succeeds - updates
TASK_INDEX.json. A failure at either step never silently reuses the
reserved task ID; see docs/tasks.md."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from forgeops.core.git import get_branch_state, get_head
from forgeops.core.paths import is_protected_reference_path
from forgeops.core.timestamps import Clock, iso_now
from forgeops.security.secret_scan import scan_text
from forgeops.state.atomic_write import atomic_write_text
from forgeops.state.schema import check_current_state
from forgeops.state.task_registry import (
    APPROVAL_STATE_NOT_REQUESTED,
    INDEX_RELATIVE_PATH,
    RESULT_STATE_PENDING,
    STATUS_DRAFT,
    VALIDATION_STATUS_NOT_RUN,
    TaskIndexDocument,
    TaskIndexRecord,
    TaskRecord,
    default_validation_record,
    load_index,
    save_index,
    save_task_record,
    save_validation_record,
    task_dir_for,
)
from forgeops.state.task_spec import (
    AcceptanceParseResult,
    append_imported_acceptance_section,
    is_script_like,
    parse_acceptance_content,
    read_source_file,
    render_spec_md,
)

MAX_TITLE_LENGTH = 200
CREATED_BY = "forgeops-cli"


@dataclass(frozen=True)
class ConflictItem:
    key: str
    message: str


def _secret_conflict(key: str, label: str, text: str) -> ConflictItem | None:
    # A path that never matches the allow-secret exemption zones (tests/,
    # fixtures/, examples/) - a task artifact is never exempt from its
    # own secret scan by a stray marker string.
    findings, _ = scan_text(text, relative_path=f".agent/tasks/__pending__/{label}")
    if findings:
        categories = ", ".join(sorted({f.category for f in findings}))
        return ConflictItem(key, f"{label} appears to contain secret-shaped content ({categories}) - refusing to persist it")
    return None


@dataclass(frozen=True)
class TaskCreatePlan:
    repo_root: Path
    title: str
    is_protected: bool
    is_initialized: bool
    task_id: str | None
    task_dir: Path | None
    next_task_number: int | None
    spec_content: str | None
    validation_criteria_note: str | None
    source_branch: str | None
    source_head: str | None
    conflicts: tuple[ConflictItem, ...] = field(default_factory=tuple)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


def build_task_create_plan(
    repo_root: Path,
    title: str,
    spec_file: Path | None,
    acceptance_file: Path | None,
    max_source_bytes: int,
) -> TaskCreatePlan:
    """Read-only preflight: classify every possible conflict before any
    mutation is attempted. Never writes anything, never runs a mutating
    git command."""
    conflicts: list[ConflictItem] = []

    is_protected = is_protected_reference_path(repo_root)
    if is_protected:
        conflicts.append(ConflictItem(
            "protected-reference-repo",
            f"{repo_root} is (or is beneath) the configured read-only reference repository - "
            "forgeops task create will never operate on it",
        ))

    state_check = check_current_state(repo_root / ".agent" / "CURRENT_STATE.json")
    is_initialized = state_check.valid
    if not is_initialized:
        conflicts.append(ConflictItem(
            "not-initialized",
            "this project is not an initialized ForgeOps project (.agent/CURRENT_STATE.json is missing or "
            "invalid) - run `forgeops init` first",
        ))

    normalized_title = title.strip() if title else ""
    if not normalized_title:
        conflicts.append(ConflictItem("invalid-title", "title must not be empty"))
    elif len(normalized_title) > MAX_TITLE_LENGTH:
        conflicts.append(ConflictItem("invalid-title", f"title must be at most {MAX_TITLE_LENGTH} characters"))
    elif "\n" in normalized_title or "\r" in normalized_title:
        conflicts.append(ConflictItem("invalid-title", "title must not contain newlines"))

    index = load_index(repo_root)
    if index.warning is not None:
        conflicts.append(ConflictItem(
            "index-malformed",
            f"{INDEX_RELATIVE_PATH} could not be read safely, refusing to create: {index.warning}",
        ))

    task_id: str | None = None
    task_dir: Path | None = None
    next_task_number: int | None = None
    if index.warning is None:
        next_task_number = index.next_task_number
        task_id = f"task-{next_task_number:04d}"
        task_dir = task_dir_for(repo_root, task_id)
        if task_dir.exists():
            conflicts.append(ConflictItem(
                "destination-exists",
                f"{task_dir} already exists - a previous creation attempt may have left it behind; "
                "resolve manually before retrying (forgeops never overwrites or repairs it automatically)",
            ))

    # --- spec-file / acceptance-file ingestion ---------------------------
    acceptance_criteria: list[str] | None = None
    if acceptance_file is not None:
        read = read_source_file(acceptance_file, max_source_bytes)
        if not read.ok:
            conflicts.append(ConflictItem("acceptance-file-invalid", f"--acceptance file: {read.error}"))
        else:
            assert read.content is not None
            if is_script_like(acceptance_file, read.content):
                conflicts.append(ConflictItem(
                    "acceptance-file-is-script",
                    f"--acceptance file {acceptance_file} looks like an executable script - "
                    "acceptance criteria must be plain text or JSON, not a script, in this checkpoint",
                ))
            else:
                secret_conflict = _secret_conflict("acceptance-file-secret-detected", "--acceptance file", read.content)
                if secret_conflict is not None:
                    conflicts.append(secret_conflict)
                else:
                    parsed: AcceptanceParseResult = parse_acceptance_content(acceptance_file, read.content)
                    if not parsed.ok:
                        conflicts.append(ConflictItem("acceptance-file-invalid-format", f"--acceptance file: {parsed.error}"))
                    else:
                        acceptance_criteria = parsed.criteria

    spec_content: str | None = None
    if spec_file is not None:
        read = read_source_file(spec_file, max_source_bytes)
        if not read.ok:
            conflicts.append(ConflictItem("spec-file-invalid", f"--spec-file: {read.error}"))
        else:
            assert read.content is not None
            secret_conflict = _secret_conflict("spec-file-secret-detected", "--spec-file", read.content)
            if secret_conflict is not None:
                conflicts.append(secret_conflict)
            else:
                spec_content = read.content
                if acceptance_criteria is not None:
                    spec_content = append_imported_acceptance_section(spec_content, acceptance_criteria)

    if spec_file is None and spec_content is None:
        # Default minimal template - always computed as a preview even
        # when another conflict (e.g. invalid title) blocks creation;
        # generating it is pure and never itself a source of conflicts.
        spec_content = render_spec_md(normalized_title or title or "(untitled)", acceptance_criteria)

    branch_state = get_branch_state(repo_root)
    head = get_head(repo_root)

    return TaskCreatePlan(
        repo_root=repo_root,
        title=normalized_title or title,
        is_protected=is_protected,
        is_initialized=is_initialized,
        task_id=task_id,
        task_dir=task_dir,
        next_task_number=next_task_number,
        spec_content=spec_content,
        validation_criteria_note=None,
        source_branch=branch_state.branch,
        source_head=head,
        conflicts=tuple(conflicts),
    )


@dataclass(frozen=True)
class TaskCreateOutcome:
    ok: bool
    task_id: str | None
    task_dir_created: bool
    task_json_written: bool
    spec_written: bool
    validation_written: bool
    index_written: bool
    index_error: str | None
    partial_state: dict | None


def apply_task_create(repo_root: Path, plan: TaskCreatePlan, clock: Clock | None = None) -> TaskCreateOutcome:
    """Execute the single mutating step this checkpoint performs: create
    `.agent/tasks/<task-id>/` with TASK.json, SPEC.md, and VALIDATION.json,
    then (only on full success) append one record to TASK_INDEX.json and
    advance its `next_task_number`. Callers must have already verified
    `plan.has_conflict` is False. On any failure while creating the task
    directory, whatever was created in this call is removed again
    (best-effort) so a failed attempt never leaves a half-written,
    unindexed task directory sitting at the next task ID."""
    assert plan.task_id is not None and plan.task_dir is not None and plan.next_task_number is not None

    now = iso_now(clock)
    created_paths: list[Path] = []

    def _rollback() -> None:
        for p in reversed(created_paths):
            try:
                if p.is_dir():
                    p.rmdir()
                else:
                    p.unlink(missing_ok=True)
            except OSError:
                pass  # best-effort cleanup only

    try:
        plan.task_dir.mkdir(parents=True, exist_ok=False)
        created_paths.append(plan.task_dir)
    except OSError as exc:
        return TaskCreateOutcome(
            ok=False, task_id=plan.task_id, task_dir_created=False, task_json_written=False,
            spec_written=False, validation_written=False, index_written=False, index_error=None,
            partial_state={"error": str(exc), "created": []},
        )

    task_record = TaskRecord(
        task_id=plan.task_id,
        title=plan.title,
        status=STATUS_DRAFT,
        project_root=str(repo_root),
        created_at=now,
        updated_at=now,
        created_by=CREATED_BY,
        scope_summary=f"{plan.title} (see SPEC.md for full scope)",
        accepted_checkpoint=plan.source_head,
        source_branch=plan.source_branch,
        source_head=plan.source_head,
        approval_state=APPROVAL_STATE_NOT_REQUESTED,
        validation_state=VALIDATION_STATUS_NOT_RUN,
        result_state=RESULT_STATE_PENDING,
    )
    validation_record = default_validation_record(plan.task_id, now)

    try:
        task_json_path = save_task_record(plan.task_dir, task_record)
        created_paths.append(task_json_path)
        spec_path = plan.task_dir / "SPEC.md"
        atomic_write_text(spec_path, plan.spec_content or render_spec_md(plan.title, None))
        created_paths.append(spec_path)
        validation_path = save_validation_record(plan.task_dir, validation_record)
        created_paths.append(validation_path)
    except OSError as exc:
        _rollback()
        return TaskCreateOutcome(
            ok=False, task_id=plan.task_id, task_dir_created=True, task_json_written=False,
            spec_written=False, validation_written=False, index_written=False, index_error=None,
            partial_state={"error": str(exc), "created": [str(p) for p in created_paths]},
        )

    index_record = TaskIndexRecord(
        task_id=plan.task_id,
        title=plan.title,
        status=STATUS_DRAFT,
        created_at=now,
        updated_at=now,
        path=str(Path(".agent") / "tasks" / plan.task_id),
        approval_state=APPROVAL_STATE_NOT_REQUESTED,
        validation_state=VALIDATION_STATUS_NOT_RUN,
        result_state=RESULT_STATE_PENDING,
    )

    index_written = False
    index_error: str | None = None
    fresh_index = load_index(repo_root)
    if fresh_index.warning is not None:
        index_error = fresh_index.warning
    else:
        try:
            save_index(repo_root, TaskIndexDocument(
                schema_version=fresh_index.schema_version,
                next_task_number=fresh_index.next_task_number + 1,
                records=[*fresh_index.records, index_record],
            ))
            index_written = True
        except OSError as exc:
            index_error = str(exc)

    return TaskCreateOutcome(
        ok=True, task_id=plan.task_id, task_dir_created=True, task_json_written=True,
        spec_written=True, validation_written=True, index_written=index_written,
        index_error=index_error, partial_state=None,
    )
