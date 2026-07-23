"""`forgeops task create|show|list|validate|close` - the persistent Task
Specification Engine. Stores task intent, scope, acceptance criteria,
validation expectations, and final outcome under `.agent/tasks/`,
outside conversational context. See docs/tasks.md for the directory
contract, ID generation, lifecycle, and explicit non-goals (no agent
assignment, no agent execution, no parallel routing, no automatic
worktree creation, no approvals, no merges).

Follows the same shape as `forgeops/cli/worktree.py`: five `run_*`
entry points sharing one `render_human`, dispatching on
`result.command`. `task create` and `task close` are the two mutating
commands (both support `--dry-run`; `task close` additionally requires
`--confirm`) - `task show`/`task list`/`task validate` are read-only."""
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
    lines.append(f"worktree_id: {task.get('worktree_id')}  agent_id: {task.get('agent_id')}")
    lines.append(f"approval_state: {task.get('approval_state')}")
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
                f"worktree={t['worktree_id']} agent={t['agent_id']} updated={t['updated_at']}"
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
