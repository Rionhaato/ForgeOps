"""`forgeops task create|show|list|validate|close|assign|unassign|
assign-agent|unassign-agent` - the persistent Task Specification Engine
plus its worktree- and agent-ownership layers. Stores task intent,
scope, acceptance criteria, validation expectations, final outcome,
worktree ownership, and (this checkpoint) agent ownership under
`.agent/tasks/`, outside conversational context. See docs/tasks.md and
docs/agents.md for the directory contracts, ID generation, lifecycle,
and explicit non-goals (no agent execution, no session launching, no
parallel routing, no automatic worktree creation, no approvals, no
merges).

Follows the same shape as `forgeops/cli/worktree.py`: nine `run_*`
entry points sharing one `render_human`, dispatching on
`result.command`. `task create`, `task assign`, `task close`, and `task
assign-agent` are mutating without a `--confirm` requirement (only
`--dry-run`) - additive, reversible operations, mirroring `task
create`/`worktree create`'s own shape; `task close`/`task unassign`/
`task unassign-agent` additionally require `--confirm`, mirroring
`worktree remove`'s destructive-operation shape instead - `task
show`/`task list`/`task validate` are read-only."""
from __future__ import annotations

from pathlib import Path

from forgeops.core import exit_codes
from forgeops.core.config import ConfigError, load_config
from forgeops.core.git import git_version
from forgeops.core.paths import RepoNotFoundError, is_protected_reference_path, resolve_repo_root
from forgeops.core.result import Check, CommandResult
from forgeops.core.timestamps import Clock, iso_now
from forgeops.reporting.logs import LogWriter
from forgeops.state.schema import check_current_state
from forgeops.state.task_close import apply_task_close, build_task_close_plan
from forgeops.state.task_create import apply_task_create, build_task_create_plan
from forgeops.state.task_approval import (
    apply_task_approve,
    apply_task_cancel_approval,
    apply_task_reject,
    apply_task_request_approval,
    build_task_approve_plan,
    build_task_cancel_approval_plan,
    build_task_reject_plan,
    build_task_request_approval_plan,
)
from forgeops.state.task_ownership import (
    apply_task_assign,
    apply_task_assign_agent,
    apply_task_unassign,
    apply_task_unassign_agent,
    build_task_assign_agent_plan,
    build_task_assign_plan,
    build_task_unassign_agent_plan,
    build_task_unassign_plan,
)
from forgeops.state.task_execution import (
    DEFAULT_RUN_TIMEOUT_SECONDS,
    apply_task_run,
    build_task_run_plan,
)
from forgeops.state.task_registry import (
    TASKS_DIR_RELATIVE,
    TASK_ID_RE,
    load_index,
    task_dir_for,
)
from forgeops.state.task_spec import REQUIRED_SPEC_HEADINGS, parse_markdown_sections
from forgeops.state.task_validate import validate_task
from forgeops.worktrees.git_worktree import resolve_commit

SCHEMA_VERSION = 1


def _no_git_result(command: str, clock: Clock | None) -> CommandResult:
    return CommandResult(
        command=command, schema_version=SCHEMA_VERSION, generated_at=iso_now(clock),
        repo_root=None, exit_code=exit_codes.COMMAND_EXECUTION_FAILURE,
        summary=f"{command}: git executable not found or unresponsive (exit {exit_codes.COMMAND_EXECUTION_FAILURE})",
        checks=[Check("git-available", "git executable", "fail", "git was not found on PATH or did not respond")],
    )


def _repo_not_found_result(command: str, exc: RepoNotFoundError, clock: Clock | None) -> CommandResult:
    return CommandResult(
        command=command, schema_version=SCHEMA_VERSION, generated_at=iso_now(clock),
        repo_root=None, exit_code=exit_codes.REPO_NOT_FOUND, summary=str(exc),
        checks=[Check("repo-discovery", "Repository discovery", "fail", str(exc))],
    )


def _repo_level_block(repo_root: Path, command: str, clock: Clock | None, write_log: bool) -> CommandResult | None:
    """Shared gate for every task command: refuse a protected reference
    repository, and require an initialized ForgeOps project
    (`.agent/CURRENT_STATE.json` present and schema-valid) - the Task
    Specification Engine has nowhere safe to persist state otherwise.
    Returns None when neither condition blocks."""
    if is_protected_reference_path(repo_root):
        checks = [
            Check("repo-discovery", "Repository discovery", "pass", str(repo_root)),
            Check(
                "reference-repo-protection", "Read-only reference repository protection", "blocked",
                f"{repo_root} is (or is beneath) the configured read-only reference repository - "
                f"forgeops {command.replace('-', ' ')} will never operate on it",
            ),
        ]
        exit_code = exit_codes.BLOCKED
        return _finish(repo_root, command, checks, {"action": "blocked"}, exit_code,
                        f"{command}: refused - {repo_root} is the read-only reference repository (exit {exit_code})",
                        clock, write_log)

    state_check = check_current_state(repo_root / ".agent" / "CURRENT_STATE.json")
    if not state_check.valid:
        detail = state_check.error or (
            f"missing keys: {', '.join(state_check.missing_keys)}" if state_check.missing_keys
            else ".agent/CURRENT_STATE.json does not exist"
        )
        checks = [
            Check("repo-discovery", "Repository discovery", "pass", str(repo_root)),
            Check(
                "project-initialized", "ForgeOps project initialization", "blocked",
                f"this project is not an initialized ForgeOps project ({detail}) - run `forgeops init` first",
            ),
        ]
        exit_code = exit_codes.BLOCKED
        return _finish(repo_root, command, checks, {"action": "blocked"}, exit_code,
                        f"{command}: refused - project is not initialized (exit {exit_code})",
                        clock, write_log)
    return None


def _max_source_bytes(repo_root: Path) -> tuple[int, Check | None]:
    try:
        return load_config(repo_root)["secret_scan_max_file_bytes"], None
    except ConfigError as exc:
        return 2 * 1024 * 1024, Check("config-validity", "Configuration validity", "warning", str(exc))


# --- task create -----------------------------------------------------------


def run_task_create(
    title: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    dry_run: bool = False,
    spec_file: str | None = None,
    acceptance_file: str | None = None,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("task-create", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("task-create", exc, clock)

    blocked = _repo_level_block(repo_root, "task-create", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    max_bytes, config_warning = _max_source_bytes(repo_root)
    if config_warning is not None:
        checks.append(config_warning)

    spec_path = Path(spec_file) if spec_file else None
    acceptance_path = Path(acceptance_file) if acceptance_file else None

    plan = build_task_create_plan(repo_root, title, spec_path, acceptance_path, max_bytes)

    if plan.conflicts:
        for c in plan.conflicts:
            checks.append(Check(f"preflight-{c.key}", f"Preflight: {c.key}", "blocked", c.message))
    else:
        checks.append(Check("preflight", "Preflight", "pass", "no conflicts detected"))

    data: dict = {
        "dry_run": dry_run,
        "title": plan.title,
        "task_id": plan.task_id,
        "task_path": str(Path(".agent") / "tasks" / plan.task_id) if plan.task_id else None,
        "source_branch": plan.source_branch,
        "source_head": plan.source_head,
        "conflicts": [{"key": c.key, "message": c.message} for c in plan.conflicts],
        "would_proceed": not plan.has_conflict,
    }

    if plan.has_conflict:
        data["action"] = "blocked"
        exit_code = exit_codes.BLOCKED
        summary = f"task create: blocked by {len(plan.conflicts)} conflict(s) (exit {exit_code})"
        return _finish(repo_root, "task-create", checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        data["action"] = "would_create_task"
        checks.append(Check(
            "write", "Task creation", "informational",
            f"dry-run: would create {data['task_path']} with status 'draft' - no file was written",
        ))
        exit_code = exit_codes.SUCCESS
        summary = f"task create: dry-run, would create {plan.task_id} (exit {exit_code})"
        return _finish(repo_root, "task-create", checks, data, exit_code, summary, clock, write_log)

    outcome = apply_task_create(repo_root, plan, clock)
    if not outcome.ok:
        data["action"] = "creation_failed"
        data["partial_state"] = outcome.partial_state
        data["manual_recovery_recommendation"] = (
            f"Inspect `{task_dir_for(repo_root, plan.task_id)}` directly. forgeops does not repair or "
            "reuse a partially-created task ID automatically - resolve manually before retrying."
        )
        checks.append(Check("write", "Task creation", "fail", f"task creation did not fully complete: {outcome.partial_state}"))
        exit_code = exit_codes.COMMAND_EXECUTION_FAILURE
        summary = f"task create: creation failed for {plan.task_id} (exit {exit_code})"
        return _finish(repo_root, "task-create", checks, data, exit_code, summary, clock, write_log)

    data["action"] = "created_task"
    data["index_written"] = outcome.index_written
    checks.append(Check("write", "Task creation", "pass", f"created {data['task_path']} with status 'draft'"))
    if outcome.index_error:
        data["index_error"] = outcome.index_error
        checks.append(Check("index-write", "Task index write", "warning", f"task created but index was not updated: {outcome.index_error}"))
        exit_code = exit_codes.WARNINGS_PRESENT
    else:
        checks.append(Check("index-write", "Task index write", "pass", "index updated"))
        exit_code = exit_codes.SUCCESS
    summary = f"task create: created {plan.task_id} (exit {exit_code})"
    return _finish(repo_root, "task-create", checks, data, exit_code, summary, clock, write_log)


# --- task show ---------------------------------------------------------------


def run_task_show(
    task_id: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("task-show", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("task-show", exc, clock)

    blocked = _repo_level_block(repo_root, "task-show", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    outcome = validate_task(repo_root, task_id, True, resolve_commit)

    if not outcome.found and any(i.key in ("invalid-task-id", "not-found") for i in outcome.issues):
        for i in outcome.issues:
            checks.append(Check(f"lookup-{i.key}", f"Lookup: {i.key}", "blocked", i.message))
        exit_code = exit_codes.BLOCKED
        data = {"found": False, "task_id": task_id}
        summary = f"task show: '{task_id}' not found (exit {exit_code})"
        return _finish(repo_root, "task-show", checks, data, exit_code, summary, clock, write_log)

    for i in outcome.issues:
        checks.append(Check(f"consistency-{i.key}", f"Consistency: {i.key}", "warning", i.message))
    if not outcome.issues:
        checks.append(Check("consistency", "Artifact consistency", "pass", "no issues detected"))

    record = outcome.task_record
    vrecord = outcome.validation_record
    sections = parse_markdown_sections(outcome.spec_text, REQUIRED_SPEC_HEADINGS) if outcome.spec_text else {}

    data = {
        "found": True,
        "task_id": task_id,
        "in_index": outcome.in_index,
        "task": record.to_dict() if record is not None else None,
        "spec_sections": sections,
        "validation": vrecord.to_dict() if vrecord is not None else None,
        "result_present": outcome.result_text is not None,
        "result_content": outcome.result_text,
        "issues": [{"key": i.key, "message": i.message, "severity": i.severity} for i in outcome.issues],
    }
    exit_code = exit_codes.WARNINGS_PRESENT if outcome.issues else exit_codes.SUCCESS
    summary = f"task show: {task_id} ({record.status if record else 'unknown'}) (exit {exit_code})"
    return _finish(repo_root, "task-show", checks, data, exit_code, summary, clock, write_log)


# --- task list -----------------------------------------------------------------


def run_task_list(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    status_filter: str | None = None,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("task-list", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("task-list", exc, clock)

    blocked = _repo_level_block(repo_root, "task-list", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    index = load_index(repo_root)
    if index.warning:
        checks.append(Check("index-schema", "Task index schema", "warning", index.warning))

    records = list(index.records) if index.warning is None else []
    if status_filter:
        records = [r for r in records if r.status == status_filter]

    stale = [r for r in (index.records if index.warning is None else []) if not task_dir_for(repo_root, r.task_id).is_dir()]
    if stale:
        checks.append(Check(
            "index-stale-entries", "Task index stale entries", "warning",
            f"{len(stale)} indexed task(s) with no directory on disk: {', '.join(r.task_id for r in stale)}",
        ))

    tasks_root = repo_root / TASKS_DIR_RELATIVE
    on_disk_ids: set[str] = set()
    if tasks_root.is_dir():
        for entry in tasks_root.iterdir():
            if entry.is_dir() and TASK_ID_RE.match(entry.name):
                on_disk_ids.add(entry.name)
    indexed_ids = {r.task_id for r in index.records} if index.warning is None else set()
    unindexed = sorted(on_disk_ids - indexed_ids)
    if unindexed:
        checks.append(Check(
            "unindexed-directories", "Unindexed task directories", "warning",
            f"{len(unindexed)} task director(y/ies) on disk not present in the index: {', '.join(unindexed)}",
        ))

    data = {
        "status_filter": status_filter,
        "tasks": [r.to_dict() for r in records],
        "total": len(records),
        "stale_index_entries": [r.task_id for r in stale],
        "unindexed_directories": unindexed,
    }
    exit_code = exit_codes.WARNINGS_PRESENT if (index.warning or stale or unindexed) else exit_codes.SUCCESS
    summary = f"task list: {len(records)} task(s) (exit {exit_code})"
    return _finish(repo_root, "task-list", checks, data, exit_code, summary, clock, write_log)


# --- task validate ---------------------------------------------------------------


def run_task_validate(
    task_id: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("task-validate", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("task-validate", exc, clock)

    blocked = _repo_level_block(repo_root, "task-validate", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    outcome = validate_task(repo_root, task_id, True, resolve_commit)

    for i in outcome.blockers:
        checks.append(Check(f"blocker-{i.key}", f"Blocker: {i.key}", "blocked", i.message))
    for i in outcome.warnings:
        checks.append(Check(f"warning-{i.key}", f"Warning: {i.key}", "warning", i.message))
    if not outcome.issues:
        checks.append(Check("validate", "Task validation", "pass", "no issues detected"))

    data = {
        "task_id": task_id,
        "found": outcome.found,
        "blockers": [{"key": i.key, "message": i.message} for i in outcome.blockers],
        "warnings": [{"key": i.key, "message": i.message} for i in outcome.warnings],
        "valid": not outcome.has_blockers,
    }
    exit_code = (
        exit_codes.BLOCKED if outcome.has_blockers
        else exit_codes.WARNINGS_PRESENT if outcome.warnings
        else exit_codes.SUCCESS
    )
    summary = f"task validate: {task_id} - {len(outcome.blockers)} blocker(s), {len(outcome.warnings)} warning(s) (exit {exit_code})"
    return _finish(repo_root, "task-validate", checks, data, exit_code, summary, clock, write_log)


# --- task close -----------------------------------------------------------------


def run_task_close(
    task_id: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    dry_run: bool = False,
    confirm: bool = False,
    result_file: str | None = None,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("task-close", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("task-close", exc, clock)

    blocked = _repo_level_block(repo_root, "task-close", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    max_bytes, config_warning = _max_source_bytes(repo_root)
    if config_warning is not None:
        checks.append(config_warning)

    result_path = Path(result_file) if result_file else None
    plan = build_task_close_plan(repo_root, task_id, result_path, max_bytes, True, resolve_commit)

    if plan.conflicts:
        for c in plan.conflicts:
            checks.append(Check(f"preflight-{c.key}", f"Preflight: {c.key}", "blocked", c.message))
    else:
        checks.append(Check("preflight", "Preflight", "pass", "no conflicts detected"))

    data: dict = {
        "dry_run": dry_run,
        "confirm": confirm,
        "task_id": task_id,
        "current_status": plan.validation.task_record.status if plan.validation.task_record else None,
        "planned_status": plan.planned_status,
        "conflicts": [{"key": c.key, "message": c.message} for c in plan.conflicts],
        "would_proceed": not plan.has_conflict,
    }

    if plan.has_conflict:
        data["action"] = "blocked"
        exit_code = exit_codes.BLOCKED
        summary = f"task close: blocked by {len(plan.conflicts)} conflict(s) (exit {exit_code})"
        return _finish(repo_root, "task-close", checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        data["action"] = "would_close_task"
        checks.append(Check(
            "write", "Task closure", "informational",
            f"dry-run: would transition '{task_id}' to status '{plan.planned_status}' and write RESULT.md - "
            "no file was written",
        ))
        exit_code = exit_codes.SUCCESS
        summary = f"task close: dry-run, would close {task_id} as '{plan.planned_status}' (exit {exit_code})"
        return _finish(repo_root, "task-close", checks, data, exit_code, summary, clock, write_log)

    if not confirm:
        data["action"] = "confirmation_required"
        checks.append(Check(
            "confirmation-required", "Confirmation required", "blocked",
            "closure is a mutating operation - pass --confirm to execute, or --dry-run to preview with zero mutation",
        ))
        exit_code = exit_codes.BLOCKED
        summary = f"task close: confirmation required for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-close", checks, data, exit_code, summary, clock, write_log)

    outcome = apply_task_close(repo_root, plan, clock)
    if not outcome.ok:
        data["action"] = "closure_failed"
        data["partial_state"] = outcome.partial_state
        data["manual_recovery_recommendation"] = (
            f"Inspect `{plan.validation.task_dir}` directly - RESULT.md/TASK.json may be partially written. "
            "forgeops does not retry or force-replace automatically; resolve manually before retrying."
        )
        checks.append(Check("write", "Task closure", "fail", f"task closure did not fully complete: {outcome.partial_state}"))
        exit_code = exit_codes.COMMAND_EXECUTION_FAILURE
        summary = f"task close: closure failed for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-close", checks, data, exit_code, summary, clock, write_log)

    data["action"] = f"closed_as_{outcome.new_status}"
    data["new_status"] = outcome.new_status
    data["index_written"] = outcome.index_written
    checks.append(Check("write", "Task closure", "pass", f"closed '{task_id}' as '{outcome.new_status}'"))
    if outcome.index_error:
        data["index_error"] = outcome.index_error
        checks.append(Check("index-write", "Task index write", "warning", f"task closed but index was not updated: {outcome.index_error}"))
        exit_code = exit_codes.WARNINGS_PRESENT
    else:
        checks.append(Check("index-write", "Task index write", "pass", "index updated"))
        exit_code = exit_codes.SUCCESS
    summary = f"task close: closed {task_id} as '{outcome.new_status}' (exit {exit_code})"
    return _finish(repo_root, "task-close", checks, data, exit_code, summary, clock, write_log)


# --- task assign -----------------------------------------------------------------


def run_task_assign(
    task_id: str,
    worktree_name: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    dry_run: bool = False,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("task-assign", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("task-assign", exc, clock)

    blocked = _repo_level_block(repo_root, "task-assign", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    plan = build_task_assign_plan(repo_root, task_id, worktree_name)

    if plan.conflicts:
        for c in plan.conflicts:
            checks.append(Check(f"preflight-{c.key}", f"Preflight: {c.key}", "blocked", c.message))
    else:
        checks.append(Check("preflight", "Preflight", "pass", "no conflicts detected"))

    data: dict = {
        "dry_run": dry_run,
        "task_id": task_id,
        "worktree_name": worktree_name,
        "conflicts": [{"key": c.key, "message": c.message} for c in plan.conflicts],
        "would_proceed": not plan.has_conflict,
    }

    if plan.has_conflict:
        data["action"] = "blocked"
        exit_code = exit_codes.BLOCKED
        summary = f"task assign: blocked by {len(plan.conflicts)} conflict(s) (exit {exit_code})"
        return _finish(repo_root, "task-assign", checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        data["action"] = "would_assign"
        checks.append(Check(
            "write", "Task assignment", "informational",
            f"dry-run: would assign worktree '{worktree_name}' to task '{task_id}' - no file was written",
        ))
        exit_code = exit_codes.SUCCESS
        summary = f"task assign: dry-run, would assign {worktree_name} to {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-assign", checks, data, exit_code, summary, clock, write_log)

    outcome = apply_task_assign(repo_root, plan, clock)
    if not outcome.ok:
        data["action"] = "assignment_failed"
        data["partial_state"] = outcome.partial_state
        data["manual_recovery_recommendation"] = (
            f"Inspect `{task_dir_for(repo_root, task_id)}/TASK.json` and the worktree registry directly - "
            "ownership may be partially updated. forgeops does not retry or force-replace automatically; "
            "resolve manually before retrying."
        )
        checks.append(Check("write", "Task assignment", "fail", f"assignment did not fully complete: {outcome.partial_state}"))
        exit_code = exit_codes.COMMAND_EXECUTION_FAILURE
        summary = f"task assign: assignment failed for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-assign", checks, data, exit_code, summary, clock, write_log)

    data["action"] = "assigned"
    data["index_written"] = outcome.index_written
    checks.append(Check("write", "Task assignment", "pass", f"assigned worktree '{worktree_name}' to task '{task_id}'"))
    if outcome.index_error:
        data["index_error"] = outcome.index_error
        checks.append(Check("index-write", "Task index write", "warning", f"assignment succeeded but index was not updated: {outcome.index_error}"))
        exit_code = exit_codes.WARNINGS_PRESENT
    else:
        checks.append(Check("index-write", "Task index write", "pass", "index updated"))
        exit_code = exit_codes.SUCCESS
    summary = f"task assign: assigned {worktree_name} to {task_id} (exit {exit_code})"
    return _finish(repo_root, "task-assign", checks, data, exit_code, summary, clock, write_log)


# --- task unassign -----------------------------------------------------------------


def run_task_unassign(
    task_id: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    dry_run: bool = False,
    confirm: bool = False,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("task-unassign", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("task-unassign", exc, clock)

    blocked = _repo_level_block(repo_root, "task-unassign", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    plan = build_task_unassign_plan(repo_root, task_id)

    if plan.conflicts:
        for c in plan.conflicts:
            checks.append(Check(f"preflight-{c.key}", f"Preflight: {c.key}", "blocked", c.message))
    else:
        checks.append(Check("preflight", "Preflight", "pass", "no conflicts detected"))

    data: dict = {
        "dry_run": dry_run,
        "confirm": confirm,
        "task_id": task_id,
        "current_worktree": plan.task_record.worktree_id if plan.task_record else None,
        "conflicts": [{"key": c.key, "message": c.message} for c in plan.conflicts],
        "would_proceed": not plan.has_conflict,
    }

    if plan.has_conflict:
        data["action"] = "blocked"
        exit_code = exit_codes.BLOCKED
        summary = f"task unassign: blocked by {len(plan.conflicts)} conflict(s) (exit {exit_code})"
        return _finish(repo_root, "task-unassign", checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        data["action"] = "would_unassign"
        checks.append(Check(
            "write", "Task unassignment", "informational",
            f"dry-run: would unassign worktree '{data['current_worktree']}' from task '{task_id}' - no file was written",
        ))
        exit_code = exit_codes.SUCCESS
        summary = f"task unassign: dry-run, would unassign {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-unassign", checks, data, exit_code, summary, clock, write_log)

    if not confirm:
        data["action"] = "confirmation_required"
        checks.append(Check(
            "confirmation-required", "Confirmation required", "blocked",
            "unassignment is a mutating operation - pass --confirm to execute, or --dry-run to preview with zero mutation",
        ))
        exit_code = exit_codes.BLOCKED
        summary = f"task unassign: confirmation required for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-unassign", checks, data, exit_code, summary, clock, write_log)

    outcome = apply_task_unassign(repo_root, plan, clock)
    if not outcome.ok:
        data["action"] = "unassignment_failed"
        data["partial_state"] = outcome.partial_state
        data["manual_recovery_recommendation"] = (
            f"Inspect `{task_dir_for(repo_root, task_id)}/TASK.json` and the worktree registry directly - "
            "ownership may be partially updated. forgeops does not retry or force-replace automatically; "
            "resolve manually before retrying."
        )
        checks.append(Check("write", "Task unassignment", "fail", f"unassignment did not fully complete: {outcome.partial_state}"))
        exit_code = exit_codes.COMMAND_EXECUTION_FAILURE
        summary = f"task unassign: unassignment failed for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-unassign", checks, data, exit_code, summary, clock, write_log)

    data["action"] = "unassigned"
    data["index_written"] = outcome.index_written
    checks.append(Check("write", "Task unassignment", "pass", f"unassigned worktree from task '{task_id}'"))
    if not outcome.worktree_registry_written:
        checks.append(Check(
            "worktree-registry-note", "Worktree registry", "informational",
            "no matching active worktree registry record was found to clear - only TASK.json was updated "
            "(the worktree was likely removed independently)",
        ))
    if outcome.index_error:
        data["index_error"] = outcome.index_error
        checks.append(Check("index-write", "Task index write", "warning", f"unassignment succeeded but index was not updated: {outcome.index_error}"))
        exit_code = exit_codes.WARNINGS_PRESENT
    else:
        checks.append(Check("index-write", "Task index write", "pass", "index updated"))
        exit_code = exit_codes.SUCCESS
    summary = f"task unassign: unassigned {task_id} (exit {exit_code})"
    return _finish(repo_root, "task-unassign", checks, data, exit_code, summary, clock, write_log)


# --- task assign-agent -----------------------------------------------------------


def run_task_assign_agent(
    task_id: str,
    agent_id: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    dry_run: bool = False,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("task-assign-agent", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("task-assign-agent", exc, clock)

    blocked = _repo_level_block(repo_root, "task-assign-agent", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    plan = build_task_assign_agent_plan(repo_root, task_id, agent_id)

    if plan.conflicts:
        for c in plan.conflicts:
            checks.append(Check(f"preflight-{c.key}", f"Preflight: {c.key}", "blocked", c.message))
    else:
        checks.append(Check("preflight", "Preflight", "pass", "no conflicts detected"))
    for w in plan.warnings:
        checks.append(Check(f"preflight-warning-{w.key}", f"Warning: {w.key}", "warning", w.message))

    data: dict = {
        "dry_run": dry_run,
        "task_id": task_id,
        "agent_id": agent_id,
        "conflicts": [{"key": c.key, "message": c.message} for c in plan.conflicts],
        "warnings": [{"key": w.key, "message": w.message} for w in plan.warnings],
        "would_proceed": not plan.has_conflict,
    }

    if plan.has_conflict:
        data["action"] = "blocked"
        exit_code = exit_codes.BLOCKED
        summary = f"task assign-agent: blocked by {len(plan.conflicts)} conflict(s) (exit {exit_code})"
        return _finish(repo_root, "task-assign-agent", checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        data["action"] = "would_assign_agent"
        checks.append(Check(
            "write", "Task agent assignment", "informational",
            f"dry-run: would assign agent '{agent_id}' to task '{task_id}' - no file was written",
        ))
        exit_code = exit_codes.WARNINGS_PRESENT if plan.warnings else exit_codes.SUCCESS
        summary = f"task assign-agent: dry-run, would assign {agent_id} to {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-assign-agent", checks, data, exit_code, summary, clock, write_log)

    outcome = apply_task_assign_agent(repo_root, plan, clock)
    if not outcome.ok:
        data["action"] = "assignment_failed"
        data["partial_state"] = outcome.partial_state
        data["manual_recovery_recommendation"] = (
            f"Inspect `{task_dir_for(repo_root, task_id)}/TASK.json` and the agent registry directly - "
            "ownership may be partially updated. forgeops does not retry or force-replace automatically; "
            "resolve manually before retrying."
        )
        checks.append(Check("write", "Task agent assignment", "fail", f"assignment did not fully complete: {outcome.partial_state}"))
        exit_code = exit_codes.COMMAND_EXECUTION_FAILURE
        summary = f"task assign-agent: assignment failed for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-assign-agent", checks, data, exit_code, summary, clock, write_log)

    data["action"] = "agent_assigned"
    data["index_written"] = outcome.index_written
    checks.append(Check("write", "Task agent assignment", "pass", f"assigned agent '{agent_id}' to task '{task_id}'"))
    warnings_present = bool(plan.warnings)
    if outcome.index_error:
        data["index_error"] = outcome.index_error
        checks.append(Check("index-write", "Task index write", "warning", f"assignment succeeded but index was not updated: {outcome.index_error}"))
        warnings_present = True
    else:
        checks.append(Check("index-write", "Task index write", "pass", "index updated"))
    exit_code = exit_codes.WARNINGS_PRESENT if warnings_present else exit_codes.SUCCESS
    summary = f"task assign-agent: assigned {agent_id} to {task_id} (exit {exit_code})"
    return _finish(repo_root, "task-assign-agent", checks, data, exit_code, summary, clock, write_log)


# --- task unassign-agent -----------------------------------------------------------


def run_task_unassign_agent(
    task_id: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    dry_run: bool = False,
    confirm: bool = False,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("task-unassign-agent", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("task-unassign-agent", exc, clock)

    blocked = _repo_level_block(repo_root, "task-unassign-agent", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    plan = build_task_unassign_agent_plan(repo_root, task_id)

    if plan.conflicts:
        for c in plan.conflicts:
            checks.append(Check(f"preflight-{c.key}", f"Preflight: {c.key}", "blocked", c.message))
    else:
        checks.append(Check("preflight", "Preflight", "pass", "no conflicts detected"))

    data: dict = {
        "dry_run": dry_run,
        "confirm": confirm,
        "task_id": task_id,
        "current_agent": plan.task_record.agent_id if plan.task_record else None,
        "conflicts": [{"key": c.key, "message": c.message} for c in plan.conflicts],
        "would_proceed": not plan.has_conflict,
    }

    if plan.has_conflict:
        data["action"] = "blocked"
        exit_code = exit_codes.BLOCKED
        summary = f"task unassign-agent: blocked by {len(plan.conflicts)} conflict(s) (exit {exit_code})"
        return _finish(repo_root, "task-unassign-agent", checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        data["action"] = "would_unassign_agent"
        checks.append(Check(
            "write", "Task agent unassignment", "informational",
            f"dry-run: would unassign agent '{data['current_agent']}' from task '{task_id}' - no file was written",
        ))
        exit_code = exit_codes.SUCCESS
        summary = f"task unassign-agent: dry-run, would unassign {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-unassign-agent", checks, data, exit_code, summary, clock, write_log)

    if not confirm:
        data["action"] = "confirmation_required"
        checks.append(Check(
            "confirmation-required", "Confirmation required", "blocked",
            "unassignment is a mutating operation - pass --confirm to execute, or --dry-run to preview with zero mutation",
        ))
        exit_code = exit_codes.BLOCKED
        summary = f"task unassign-agent: confirmation required for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-unassign-agent", checks, data, exit_code, summary, clock, write_log)

    outcome = apply_task_unassign_agent(repo_root, plan, clock)
    if not outcome.ok:
        data["action"] = "unassignment_failed"
        data["partial_state"] = outcome.partial_state
        data["manual_recovery_recommendation"] = (
            f"Inspect `{task_dir_for(repo_root, task_id)}/TASK.json` and the agent registry directly - "
            "ownership may be partially updated. forgeops does not retry or force-replace automatically; "
            "resolve manually before retrying."
        )
        checks.append(Check("write", "Task agent unassignment", "fail", f"unassignment did not fully complete: {outcome.partial_state}"))
        exit_code = exit_codes.COMMAND_EXECUTION_FAILURE
        summary = f"task unassign-agent: unassignment failed for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-unassign-agent", checks, data, exit_code, summary, clock, write_log)

    data["action"] = "agent_unassigned"
    data["index_written"] = outcome.index_written
    checks.append(Check("write", "Task agent unassignment", "pass", f"unassigned agent from task '{task_id}'"))
    if not outcome.agent_registry_written:
        checks.append(Check(
            "agent-registry-note", "Agent registry", "informational",
            "no matching agent registry record was found to clear - only TASK.json was updated",
        ))
    if outcome.index_error:
        data["index_error"] = outcome.index_error
        checks.append(Check("index-write", "Task index write", "warning", f"unassignment succeeded but index was not updated: {outcome.index_error}"))
        exit_code = exit_codes.WARNINGS_PRESENT
    else:
        checks.append(Check("index-write", "Task index write", "pass", "index updated"))
        exit_code = exit_codes.SUCCESS
    summary = f"task unassign-agent: unassigned {task_id} (exit {exit_code})"
    return _finish(repo_root, "task-unassign-agent", checks, data, exit_code, summary, clock, write_log)


# --- task run -------------------------------------------------------------------


def run_task_run(
    task_id: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    actor: str = "",
    timeout: float = DEFAULT_RUN_TIMEOUT_SECONDS,
    dry_run: bool = False,
    confirm: bool = False,
    executable_override: list[str] | None = None,
) -> CommandResult:
    """The single most consequential command in ForgeOps: launches a
    real `claude`/`codex` subprocess capable of arbitrary file edits and
    shell commands. Confirmation-gated like `task close`/`worktree
    remove` even though nothing is deleted - `--dry-run` needs no
    confirmation and launches nothing; a real run without `--confirm`
    only previews (zero mutation, zero process launched).
    `executable_override` exists purely for tests - see
    `forgeops/state/task_execution.py`'s module docstring."""
    if git_version() is None:
        return _no_git_result("task-run", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("task-run", exc, clock)

    blocked = _repo_level_block(repo_root, "task-run", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    plan = build_task_run_plan(repo_root, task_id, actor, timeout)

    if plan.conflicts:
        for c in plan.conflicts:
            checks.append(Check(f"preflight-{c.key}", f"Preflight: {c.key}", "blocked", c.message))
    else:
        checks.append(Check("preflight", "Preflight", "pass", "no conflicts detected"))

    data: dict = {
        "dry_run": dry_run,
        "confirm": confirm,
        "task_id": task_id,
        "actor": actor,
        "timeout": timeout,
        "agent_id": plan.agent_record.agent_id if plan.agent_record else None,
        "worktree_id": plan.worktree_record.name if plan.worktree_record else None,
        "resolved_executable": plan.resolved_executable,
        "conflicts": [{"key": c.key, "message": c.message} for c in plan.conflicts],
        "would_proceed": not plan.has_conflict,
    }

    if plan.has_conflict:
        data["action"] = "blocked"
        exit_code = exit_codes.BLOCKED
        summary = f"task run: blocked by {len(plan.conflicts)} conflict(s) (exit {exit_code})"
        return _finish(repo_root, "task-run", checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        data["action"] = "would_run"
        checks.append(Check(
            "write", "Task run", "informational",
            f"dry-run: would launch '{plan.resolved_executable}' against worktree "
            f"'{data['worktree_id']}' for task '{task_id}' - no process was started",
        ))
        exit_code = exit_codes.SUCCESS
        summary = f"task run: dry-run, would run {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-run", checks, data, exit_code, summary, clock, write_log)

    if not confirm:
        data["action"] = "confirmation_required"
        checks.append(Check(
            "confirmation-required", "Confirmation required", "blocked",
            "task run launches a real, unsandboxed agent process capable of arbitrary file edits and shell "
            "commands - pass --confirm to execute, or --dry-run to preview with zero mutation",
        ))
        exit_code = exit_codes.BLOCKED
        summary = f"task run: confirmation required for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-run", checks, data, exit_code, summary, clock, write_log)

    outcome = apply_task_run(repo_root, plan, clock, executable_override, write_log)
    if not outcome.ok:
        data["action"] = "run_failed"
        data["partial_state"] = outcome.partial_state
        data["manual_recovery_recommendation"] = (
            f"Inspect `{task_dir_for(repo_root, task_id)}/TASK.json` directly - execution state may be "
            "partially updated. forgeops does not retry or force-replace automatically; resolve manually "
            "before retrying."
        )
        checks.append(Check("write", "Task run", "fail", f"run did not fully complete: {outcome.partial_state}"))
        exit_code = exit_codes.COMMAND_EXECUTION_FAILURE
        summary = f"task run: failed for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-run", checks, data, exit_code, summary, clock, write_log)

    data["exit_code"] = outcome.exit_code
    data["timed_out"] = outcome.timed_out
    data["log_path"] = outcome.log_path
    data["index_written"] = outcome.index_written

    process_succeeded = outcome.exit_code == 0 and not outcome.timed_out
    if process_succeeded:
        data["action"] = "run_completed"
        checks.append(Check(
            "write", "Task run", "pass",
            f"'{task_id}' ran to completion (exit 0); status is now 'validation_pending'",
        ))
        warnings_present = False
    else:
        data["action"] = "run_did_not_succeed"
        reason = "timed out" if outcome.timed_out else f"exited {outcome.exit_code}"
        checks.append(Check(
            "write", "Task run", "warning",
            f"'{task_id}' {reason}; status is now 'blocked' - see {outcome.log_path}",
        ))
        warnings_present = True

    if outcome.index_error:
        data["index_error"] = outcome.index_error
        checks.append(Check("index-write", "Task index write", "warning", f"run succeeded but index was not updated: {outcome.index_error}"))
        warnings_present = True
    else:
        checks.append(Check("index-write", "Task index write", "pass", "index updated"))

    exit_code = exit_codes.WARNINGS_PRESENT if warnings_present else exit_codes.SUCCESS
    summary = (
        f"task run: {task_id} finished (process exit={outcome.exit_code}, "
        f"timed_out={outcome.timed_out}) (exit {exit_code})"
    )
    return _finish(repo_root, "task-run", checks, data, exit_code, summary, clock, write_log)


# --- task request-approval / approve / reject / cancel-approval --------------------


def _handle_approval_outcome(
    repo_root: Path, command: str, label: str, task_id: str,
    checks: list[Check], data: dict, outcome, clock: Clock | None, write_log: bool,
) -> CommandResult:
    """Shared success/failure handling for all four approval-mutating
    commands. Unlike `task assign`/`task assign-agent`, a successful
    outcome here is never partial: `apply_task_*` in
    `forgeops.state.task_approval` treats `TASK.json` and
    `TASK_INDEX.json` as an atomic pair and rolls back on the second
    write's failure, so `outcome.ok` is either "both written" or
    "neither retained" - no warning-only branch is possible."""
    if not outcome.ok:
        data["action"] = "approval_action_failed"
        data["partial_state"] = outcome.partial_state
        data["manual_recovery_recommendation"] = (
            f"Inspect `{task_dir_for(repo_root, task_id)}/TASK.json` and `.agent/tasks/TASK_INDEX.json` directly - "
            "approval state may be partially updated. forgeops does not retry or force-replace automatically; "
            "resolve manually before retrying."
        )
        checks.append(Check("write", label, "fail", f"{label.lower()} did not fully complete: {outcome.partial_state}"))
        exit_code = exit_codes.COMMAND_EXECUTION_FAILURE
        summary = f"{command.replace('-', ' ')}: failed for {task_id} (exit {exit_code})"
        return _finish(repo_root, command, checks, data, exit_code, summary, clock, write_log)

    data["action"] = "approval_state_changed"
    data["new_approval_state"] = outcome.new_approval_state
    data["index_written"] = outcome.index_written
    checks.append(Check("write", label, "pass", f"'{task_id}' approval_state is now '{outcome.new_approval_state}'"))
    checks.append(Check("index-write", "Task index write", "pass", "index updated"))
    exit_code = exit_codes.SUCCESS
    summary = f"{command.replace('-', ' ')}: {task_id} approval_state is now '{outcome.new_approval_state}' (exit {exit_code})"
    return _finish(repo_root, command, checks, data, exit_code, summary, clock, write_log)


def run_task_request_approval(
    task_id: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    actor: str = "",
    reason: str | None = None,
    dry_run: bool = False,
) -> CommandResult:
    """Additive/reversible, like `task assign` - no `--confirm` required,
    only `--dry-run`."""
    if git_version() is None:
        return _no_git_result("task-request-approval", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("task-request-approval", exc, clock)

    blocked = _repo_level_block(repo_root, "task-request-approval", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    plan = build_task_request_approval_plan(repo_root, task_id, actor, reason)

    if plan.conflicts:
        for c in plan.conflicts:
            checks.append(Check(f"preflight-{c.key}", f"Preflight: {c.key}", "blocked", c.message))
    else:
        checks.append(Check("preflight", "Preflight", "pass", "no conflicts detected"))

    data: dict = {
        "dry_run": dry_run,
        "task_id": task_id,
        "actor": actor,
        "reason": reason,
        "current_approval_state": plan.current_approval_state,
        "planned_approval_state": plan.planned_approval_state,
        "conflicts": [{"key": c.key, "message": c.message} for c in plan.conflicts],
        "would_proceed": not plan.has_conflict,
    }

    if plan.has_conflict:
        data["action"] = "blocked"
        exit_code = exit_codes.BLOCKED
        summary = f"task request-approval: blocked by {len(plan.conflicts)} conflict(s) (exit {exit_code})"
        return _finish(repo_root, "task-request-approval", checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        data["action"] = "would_request_approval"
        checks.append(Check(
            "write", "Approval request", "informational",
            f"dry-run: would transition '{task_id}' approval_state from '{plan.current_approval_state}' to "
            f"'{plan.planned_approval_state}' - no file was written",
        ))
        exit_code = exit_codes.SUCCESS
        summary = f"task request-approval: dry-run, would request approval for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-request-approval", checks, data, exit_code, summary, clock, write_log)

    outcome = apply_task_request_approval(repo_root, plan, clock)
    return _handle_approval_outcome(repo_root, "task-request-approval", "Approval request", task_id, checks, data, outcome, clock, write_log)


def run_task_approve(
    task_id: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    actor: str = "",
    reason: str | None = None,
    dry_run: bool = False,
    confirm: bool = False,
) -> CommandResult:
    """Confirmation-gated, like `task close` - `--dry-run` needs no
    confirmation and mutates nothing; a real run without `--confirm`
    only previews (zero mutation)."""
    if git_version() is None:
        return _no_git_result("task-approve", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("task-approve", exc, clock)

    blocked = _repo_level_block(repo_root, "task-approve", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    plan = build_task_approve_plan(repo_root, task_id, actor, reason)

    if plan.conflicts:
        for c in plan.conflicts:
            checks.append(Check(f"preflight-{c.key}", f"Preflight: {c.key}", "blocked", c.message))
    else:
        checks.append(Check("preflight", "Preflight", "pass", "no conflicts detected"))

    data: dict = {
        "dry_run": dry_run,
        "confirm": confirm,
        "task_id": task_id,
        "actor": actor,
        "reason": reason,
        "current_approval_state": plan.current_approval_state,
        "planned_approval_state": plan.planned_approval_state,
        "conflicts": [{"key": c.key, "message": c.message} for c in plan.conflicts],
        "would_proceed": not plan.has_conflict,
    }

    if plan.has_conflict:
        data["action"] = "blocked"
        exit_code = exit_codes.BLOCKED
        summary = f"task approve: blocked by {len(plan.conflicts)} conflict(s) (exit {exit_code})"
        return _finish(repo_root, "task-approve", checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        data["action"] = "would_approve"
        checks.append(Check(
            "write", "Approval", "informational",
            f"dry-run: would transition '{task_id}' approval_state from '{plan.current_approval_state}' to "
            f"'{plan.planned_approval_state}' - no file was written",
        ))
        exit_code = exit_codes.SUCCESS
        summary = f"task approve: dry-run, would approve {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-approve", checks, data, exit_code, summary, clock, write_log)

    if not confirm:
        data["action"] = "confirmation_required"
        checks.append(Check(
            "confirmation-required", "Confirmation required", "blocked",
            "approval is a mutating operation - pass --confirm to execute, or --dry-run to preview with zero mutation",
        ))
        exit_code = exit_codes.BLOCKED
        summary = f"task approve: confirmation required for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-approve", checks, data, exit_code, summary, clock, write_log)

    outcome = apply_task_approve(repo_root, plan, clock)
    return _handle_approval_outcome(repo_root, "task-approve", "Approval", task_id, checks, data, outcome, clock, write_log)


def run_task_reject(
    task_id: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    actor: str = "",
    reason: str | None = None,
    dry_run: bool = False,
    confirm: bool = False,
) -> CommandResult:
    """Confirmation-gated, like `task approve` - additionally, a reason
    is required (enforced by the preflight plan, not here) since a
    rejection with no stated reason is refused rather than silently
    accepted with an empty one."""
    if git_version() is None:
        return _no_git_result("task-reject", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("task-reject", exc, clock)

    blocked = _repo_level_block(repo_root, "task-reject", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    plan = build_task_reject_plan(repo_root, task_id, actor, reason)

    if plan.conflicts:
        for c in plan.conflicts:
            checks.append(Check(f"preflight-{c.key}", f"Preflight: {c.key}", "blocked", c.message))
    else:
        checks.append(Check("preflight", "Preflight", "pass", "no conflicts detected"))

    data: dict = {
        "dry_run": dry_run,
        "confirm": confirm,
        "task_id": task_id,
        "actor": actor,
        "reason": reason,
        "current_approval_state": plan.current_approval_state,
        "planned_approval_state": plan.planned_approval_state,
        "conflicts": [{"key": c.key, "message": c.message} for c in plan.conflicts],
        "would_proceed": not plan.has_conflict,
    }

    if plan.has_conflict:
        data["action"] = "blocked"
        exit_code = exit_codes.BLOCKED
        summary = f"task reject: blocked by {len(plan.conflicts)} conflict(s) (exit {exit_code})"
        return _finish(repo_root, "task-reject", checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        data["action"] = "would_reject"
        checks.append(Check(
            "write", "Rejection", "informational",
            f"dry-run: would transition '{task_id}' approval_state from '{plan.current_approval_state}' to "
            f"'{plan.planned_approval_state}' - no file was written",
        ))
        exit_code = exit_codes.SUCCESS
        summary = f"task reject: dry-run, would reject {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-reject", checks, data, exit_code, summary, clock, write_log)

    if not confirm:
        data["action"] = "confirmation_required"
        checks.append(Check(
            "confirmation-required", "Confirmation required", "blocked",
            "rejection is a mutating operation - pass --confirm to execute, or --dry-run to preview with zero mutation",
        ))
        exit_code = exit_codes.BLOCKED
        summary = f"task reject: confirmation required for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-reject", checks, data, exit_code, summary, clock, write_log)

    outcome = apply_task_reject(repo_root, plan, clock)
    return _handle_approval_outcome(repo_root, "task-reject", "Rejection", task_id, checks, data, outcome, clock, write_log)


def run_task_cancel_approval(
    task_id: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    actor: str = "",
    reason: str | None = None,
    dry_run: bool = False,
    confirm: bool = False,
) -> CommandResult:
    """Confirmation-gated, like `task approve`/`task reject`. Only valid
    from `pending`, back to `not_requested` - never touches task status."""
    if git_version() is None:
        return _no_git_result("task-cancel-approval", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("task-cancel-approval", exc, clock)

    blocked = _repo_level_block(repo_root, "task-cancel-approval", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    plan = build_task_cancel_approval_plan(repo_root, task_id, actor, reason)

    if plan.conflicts:
        for c in plan.conflicts:
            checks.append(Check(f"preflight-{c.key}", f"Preflight: {c.key}", "blocked", c.message))
    else:
        checks.append(Check("preflight", "Preflight", "pass", "no conflicts detected"))

    data: dict = {
        "dry_run": dry_run,
        "confirm": confirm,
        "task_id": task_id,
        "actor": actor,
        "reason": reason,
        "current_approval_state": plan.current_approval_state,
        "planned_approval_state": plan.planned_approval_state,
        "conflicts": [{"key": c.key, "message": c.message} for c in plan.conflicts],
        "would_proceed": not plan.has_conflict,
    }

    if plan.has_conflict:
        data["action"] = "blocked"
        exit_code = exit_codes.BLOCKED
        summary = f"task cancel-approval: blocked by {len(plan.conflicts)} conflict(s) (exit {exit_code})"
        return _finish(repo_root, "task-cancel-approval", checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        data["action"] = "would_cancel_approval"
        checks.append(Check(
            "write", "Cancellation", "informational",
            f"dry-run: would transition '{task_id}' approval_state from '{plan.current_approval_state}' to "
            f"'{plan.planned_approval_state}' - no file was written",
        ))
        exit_code = exit_codes.SUCCESS
        summary = f"task cancel-approval: dry-run, would cancel approval for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-cancel-approval", checks, data, exit_code, summary, clock, write_log)

    if not confirm:
        data["action"] = "confirmation_required"
        checks.append(Check(
            "confirmation-required", "Confirmation required", "blocked",
            "cancellation is a mutating operation - pass --confirm to execute, or --dry-run to preview with zero mutation",
        ))
        exit_code = exit_codes.BLOCKED
        summary = f"task cancel-approval: confirmation required for {task_id} (exit {exit_code})"
        return _finish(repo_root, "task-cancel-approval", checks, data, exit_code, summary, clock, write_log)

    outcome = apply_task_cancel_approval(repo_root, plan, clock)
    return _handle_approval_outcome(repo_root, "task-cancel-approval", "Cancellation", task_id, checks, data, outcome, clock, write_log)


# --- shared -----------------------------------------------------------------


def _finish(
    repo_root: Path,
    command: str,
    checks: list[Check],
    data: dict,
    exit_code: int,
    summary: str,
    clock: Clock | None,
    write_log: bool,
) -> CommandResult:
    result = CommandResult(
        command=command, schema_version=SCHEMA_VERSION, generated_at=iso_now(clock),
        repo_root=str(repo_root), exit_code=exit_code, summary=summary, checks=checks, data=data,
    )
    if write_log:
        LogWriter(repo_root, command, clock=clock).write(f"{command}.log", _render_log_text(result))
    return result


def _render_log_text(result: CommandResult) -> str:
    lines = [f"forgeops {result.command} - {result.generated_at}", f"repo_root: {result.repo_root}", ""]
    lines += [f"[{c.status}] {c.id}: {c.message}" for c in result.checks]
    lines.append("")
    lines.append(result.summary)
    return "\n".join(lines)


def render_human(result: CommandResult) -> str:
    if result.command == "task-create":
        return _render_human_create(result)
    if result.command == "task-show":
        return _render_human_show(result)
    if result.command == "task-list":
        return _render_human_list(result)
    if result.command == "task-validate":
        return _render_human_validate(result)
    if result.command == "task-assign":
        return _render_human_assign(result)
    if result.command == "task-unassign":
        return _render_human_unassign(result)
    if result.command == "task-assign-agent":
        return _render_human_assign_agent(result)
    if result.command == "task-unassign-agent":
        return _render_human_unassign_agent(result)
    if result.command == "task-run":
        return _render_human_run(result)
    if result.command == "task-request-approval":
        return _render_human_approval_action(result, "forgeops task request-approval")
    if result.command == "task-approve":
        return _render_human_approval_action(result, "forgeops task approve")
    if result.command == "task-reject":
        return _render_human_approval_action(result, "forgeops task reject")
    if result.command == "task-cancel-approval":
        return _render_human_approval_action(result, "forgeops task cancel-approval")
    return _render_human_close(result)


def _render_human_create(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops task create", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    d = result.data
    lines.append("")
    lines.append(f"title: {d.get('title')}")
    lines.append(f"task id: {d.get('task_id')}")
    lines.append(f"task path: {d.get('task_path')}")
    lines.append(f"would proceed: {d.get('would_proceed')}")
    if d.get("conflicts"):
        lines.append("conflicts:")
        for c in d["conflicts"]:
            lines.append(f"  - {c['key']}: {c['message']}")
    if d.get("partial_state") is not None:
        lines.append(f"partial state after failure: {d['partial_state']}")
        lines.append(f"recovery: {d.get('manual_recovery_recommendation')}")
    return "\n".join(lines)


def _render_human_show(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops task show", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    d = result.data
    lines.append("")
    if not d.get("found"):
        lines.append(f"task: {d.get('task_id')} - not found")
        return "\n".join(lines)
    task = d.get("task") or {}
    sections = d.get("spec_sections") or {}
    validation = d.get("validation") or {}
    lines.append(f"task: {task.get('task_id')} - {task.get('title')}")
    lines.append(f"status: {task.get('status')}")
    lines.append(f"objective: {sections.get('Objective', '(not read)')[:200]}")
    lines.append(f"in scope: {sections.get('In Scope', '(not read)')[:200]}")
    lines.append(f"out of scope: {sections.get('Out of Scope', '(not read)')[:200]}")
    lines.append(f"acceptance criteria: {sections.get('Acceptance Criteria', '(not read)')[:200]}")
    lines.append(f"required validation: {sections.get('Required Validation', '(not read)')[:200]}")
    lines.append(f"blockers: {task.get('blockers')}")
    worktree_id = task.get("worktree_id")
    ownership_state = "assigned" if worktree_id else "unassigned"
    lines.append(f"assigned worktree: {worktree_id}  ownership: {ownership_state}")
    agent_id = task.get("agent_id")
    agent_ownership_state = "assigned" if agent_id else "unassigned"
    lines.append(f"assigned agent: {agent_id}  agent ownership: {agent_ownership_state}")
    lines.append(f"approval_state: {task.get('approval_state')}")
    history = task.get("approval_history") or []
    if history:
        last = history[-1]
        lines.append(
            f"last approval action: {last.get('action')}  actor: {last.get('actor')}  "
            f"at: {last.get('timestamp')}"
        )
        if last.get("reason"):
            lines.append(f"last approval reason: {last.get('reason')}")
    else:
        lines.append("last approval action: (none)")
    lines.append(f"validation status: {validation.get('status')}")
    lines.append(f"result present: {d.get('result_present')}")
    if d.get("issues"):
        lines.append("issues:")
        for i in d["issues"]:
            lines.append(f"  - [{i['severity']}] {i['key']}: {i['message']}")
    return "\n".join(lines)


def _render_human_list(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops task list", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    d = result.data
    if d.get("tasks"):
        lines.append("")
        for t in d["tasks"]:
            lines.append(
                f"- {t['task_id']}: {t['title']} [{t['status']}] "
                f"approval={t['approval_state']} validation={t['validation_state']} result={t['result_state']} "
                f"assigned_worktree={t['worktree_id']} assigned_agent={t['agent_id']} updated={t['updated_at']}"
            )
    return "\n".join(lines)


def _render_human_validate(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops task validate", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    d = result.data
    lines.append("")
    lines.append(f"task: {d.get('task_id')}  valid: {d.get('valid')}")
    return "\n".join(lines)


def _render_human_assign(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops task assign", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    d = result.data
    lines.append("")
    lines.append(f"task: {d.get('task_id')}  worktree: {d.get('worktree_name')}")
    lines.append(f"would proceed: {d.get('would_proceed')}")
    if d.get("conflicts"):
        lines.append("conflicts:")
        for c in d["conflicts"]:
            lines.append(f"  - {c['key']}: {c['message']}")
    if d.get("partial_state") is not None:
        lines.append(f"partial state after failure: {d['partial_state']}")
        lines.append(f"recovery: {d.get('manual_recovery_recommendation')}")
    return "\n".join(lines)


def _render_human_unassign(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops task unassign", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    d = result.data
    lines.append("")
    lines.append(f"task: {d.get('task_id')}  current worktree: {d.get('current_worktree')}")
    lines.append(f"would proceed: {d.get('would_proceed')}")
    if d.get("conflicts"):
        lines.append("conflicts:")
        for c in d["conflicts"]:
            lines.append(f"  - {c['key']}: {c['message']}")
    if d.get("partial_state") is not None:
        lines.append(f"partial state after failure: {d['partial_state']}")
        lines.append(f"recovery: {d.get('manual_recovery_recommendation')}")
    return "\n".join(lines)


def _render_human_assign_agent(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops task assign-agent", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    d = result.data
    lines.append("")
    lines.append(f"task: {d.get('task_id')}  agent: {d.get('agent_id')}")
    lines.append(f"would proceed: {d.get('would_proceed')}")
    if d.get("conflicts"):
        lines.append("conflicts:")
        for c in d["conflicts"]:
            lines.append(f"  - {c['key']}: {c['message']}")
    if d.get("warnings"):
        lines.append("warnings:")
        for w in d["warnings"]:
            lines.append(f"  - {w['key']}: {w['message']}")
    if d.get("partial_state") is not None:
        lines.append(f"partial state after failure: {d['partial_state']}")
        lines.append(f"recovery: {d.get('manual_recovery_recommendation')}")
    return "\n".join(lines)


def _render_human_run(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops task run", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    d = result.data
    lines.append("")
    lines.append(f"task: {d.get('task_id')}  agent: {d.get('agent_id')}  worktree: {d.get('worktree_id')}")
    lines.append(f"would proceed: {d.get('would_proceed')}")
    if "exit_code" in d:
        lines.append(f"process exit code: {d.get('exit_code')}  timed out: {d.get('timed_out')}  log: {d.get('log_path')}")
    if d.get("conflicts"):
        lines.append("conflicts:")
        for c in d["conflicts"]:
            lines.append(f"  - {c['key']}: {c['message']}")
    if d.get("partial_state") is not None:
        lines.append(f"partial state after failure: {d['partial_state']}")
        lines.append(f"recovery: {d.get('manual_recovery_recommendation')}")
    return "\n".join(lines)


def _render_human_unassign_agent(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops task unassign-agent", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    d = result.data
    lines.append("")
    lines.append(f"task: {d.get('task_id')}  current agent: {d.get('current_agent')}")
    lines.append(f"would proceed: {d.get('would_proceed')}")
    if d.get("conflicts"):
        lines.append("conflicts:")
        for c in d["conflicts"]:
            lines.append(f"  - {c['key']}: {c['message']}")
    if d.get("partial_state") is not None:
        lines.append(f"partial state after failure: {d['partial_state']}")
        lines.append(f"recovery: {d.get('manual_recovery_recommendation')}")
    return "\n".join(lines)


def _render_human_approval_action(result: CommandResult, title: str) -> str:
    from forgeops.cli.render import render_checks
    lines = [title, f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    d = result.data
    lines.append("")
    lines.append(f"task: {d.get('task_id')}  actor: {d.get('actor')}")
    if d.get("reason"):
        lines.append(f"reason: {d.get('reason')}")
    lines.append(f"approval_state: {d.get('current_approval_state')} -> {d.get('planned_approval_state')}")
    lines.append(f"would proceed: {d.get('would_proceed')}")
    if d.get("conflicts"):
        lines.append("conflicts:")
        for c in d["conflicts"]:
            lines.append(f"  - {c['key']}: {c['message']}")
    if d.get("partial_state") is not None:
        lines.append(f"partial state after failure: {d['partial_state']}")
        lines.append(f"recovery: {d.get('manual_recovery_recommendation')}")
    return "\n".join(lines)


def _render_human_close(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops task close", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    d = result.data
    lines.append("")
    lines.append(f"task: {d.get('task_id')}")
    lines.append(f"current status: {d.get('current_status')}  planned status: {d.get('planned_status')}")
    lines.append(f"would proceed: {d.get('would_proceed')}")
    if d.get("conflicts"):
        lines.append("conflicts:")
        for c in d["conflicts"]:
            lines.append(f"  - {c['key']}: {c['message']}")
    if d.get("partial_state") is not None:
        lines.append(f"partial state after failure: {d['partial_state']}")
        lines.append(f"recovery: {d.get('manual_recovery_recommendation')}")
    return "\n".join(lines)
