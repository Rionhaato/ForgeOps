"""`forgeops test --targeted` - plans and (unless --plan/--dry-run) runs
tests for the current working-tree changes. Planning is read-only;
execution runs the planned test commands and writes their raw output
under logs/test/<timestamp>/, but never modifies source files and never
installs dependencies."""
from __future__ import annotations

from pathlib import Path

from forgeops.core import exit_codes
from forgeops.core.config import ConfigError, load_config
from forgeops.core.git import git_version
from forgeops.core.paths import RepoNotFoundError, resolve_repo_root
from forgeops.core.result import Check, CommandResult
from forgeops.core.timestamps import Clock, iso_now
from forgeops.detectors.changed import get_changed_files
from forgeops.reporting.logs import LogWriter
from forgeops.testing.executor import ExecutionSummary, execute_plan
from forgeops.testing.planner import TestPlan, build_full_test_plan, build_test_plan

SCHEMA_VERSION = 1
DEFAULT_CONFIG_FALLBACK = {"oversized_file_bytes": 5 * 1024 * 1024, "secret_scan_max_file_bytes": 2 * 1024 * 1024}


def run_test_targeted(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    plan_only: bool = False,
    dry_run: bool = False,
) -> CommandResult:
    if git_version() is None:
        return CommandResult(
            command="test",
            schema_version=SCHEMA_VERSION,
            generated_at=iso_now(clock),
            repo_root=None,
            exit_code=exit_codes.COMMAND_EXECUTION_FAILURE,
            summary="git executable not found or unresponsive",
            checks=[Check("git-available", "git executable", "fail", "git was not found on PATH or did not respond")],
        )

    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return CommandResult(
            command="test",
            schema_version=SCHEMA_VERSION,
            generated_at=iso_now(clock),
            repo_root=None,
            exit_code=exit_codes.REPO_NOT_FOUND,
            summary=str(exc),
            checks=[Check("repo-discovery", "Repository discovery", "fail", str(exc))],
        )

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]

    try:
        config = load_config(repo_root)
    except ConfigError as exc:
        config = dict(DEFAULT_CONFIG_FALLBACK)
        checks.append(Check("config-validity", "Configuration validity", "warning", str(exc)))

    changed = get_changed_files(repo_root)
    plan: TestPlan = build_test_plan(repo_root, changed, config)

    if not plan.changed_files:
        checks.append(Check("test-plan", "Test plan", "pass", "no changed files, nothing to test"))
    elif not plan.commands:
        checks.append(Check(
            "test-plan", "Test plan", "warning",
            "changed files present but no test command could be selected; see warnings",
        ))
    else:
        checks.append(Check(
            "test-plan", "Test plan", "pass",
            f"{len(plan.commands)} command(s) selected, scope={plan.scope}, confidence={plan.confidence}",
        ))
    for warning in plan.warnings:
        checks.append(Check("test-plan-gap", "Test plan gap", "warning", warning))

    data: dict = {"plan": plan.to_dict()}

    do_execute = not (plan_only or dry_run)

    if not do_execute:
        exit_code = exit_codes.WARNINGS_PRESENT if plan.warnings else exit_codes.SUCCESS
        mode = "plan" if plan_only else "dry-run"
        summary = f"{mode}: {len(plan.commands)} command(s), scope={plan.scope} (exit {exit_code})"
        result = CommandResult(
            command="test", schema_version=SCHEMA_VERSION, generated_at=iso_now(clock),
            repo_root=str(repo_root), exit_code=exit_code, summary=summary, checks=checks, data=data,
        )
        if write_log:
            LogWriter(repo_root, "test", clock=clock).write("test-plan.log", _render_plan_log_text(result))
        return result

    log_writer = LogWriter(repo_root, "test", clock=clock) if write_log else None
    execution: ExecutionSummary = execute_plan(repo_root, plan, log_writer)

    data["execution"] = [
        {
            "command": r.command, "cwd": r.cwd, "log_name": r.log_name, "log_path": r.log_path,
            "returncode": r.returncode, "duration_seconds": r.duration_seconds,
            "timed_out": r.timed_out, "error": r.error, "ran": r.ran, "succeeded": r.succeeded,
        }
        for r in execution.results
    ]

    for r in execution.results:
        label = " ".join(r.command)
        if r.succeeded:
            checks.append(Check("test-execution", f"ran: {label}", "pass", f"exit {r.returncode} in {r.duration_seconds}s"))
        elif r.timed_out:
            checks.append(Check("test-execution", f"ran: {label}", "fail", f"timed out after {r.duration_seconds}s"))
        elif r.error is not None:
            checks.append(Check("test-execution", f"ran: {label}", "fail", r.error))
        else:
            checks.append(Check("test-execution", f"ran: {label}", "fail", f"exit {r.returncode} in {r.duration_seconds}s"))

    exit_code = _compute_execution_exit_code(execution, plan)
    summary = (
        f"{len(execution.results)} command(s) run, "
        f"{sum(1 for r in execution.results if r.succeeded)} succeeded (exit {exit_code})"
    )

    result = CommandResult(
        command="test", schema_version=SCHEMA_VERSION, generated_at=iso_now(clock),
        repo_root=str(repo_root), exit_code=exit_code, summary=summary, checks=checks, data=data,
    )

    if write_log:
        LogWriter(repo_root, "test", clock=clock).write("test-summary.log", _render_execution_log_text(result))

    return result


def run_full_test(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    plan_only: bool = False,
    dry_run: bool = False,
) -> CommandResult:
    """`forgeops test --full` - runs the complete supported test suite(s)
    for every detected technology, ignoring changed files entirely. Does
    not touch run_test_targeted's behavior or code path at all - this is
    a parallel function sharing only the underlying TestPlan/TestCommand
    model and the executor, exactly like build_full_test_plan shares
    build_test_plan's helpers without altering it."""
    if git_version() is None:
        return CommandResult(
            command="test",
            schema_version=SCHEMA_VERSION,
            generated_at=iso_now(clock),
            repo_root=None,
            exit_code=exit_codes.COMMAND_EXECUTION_FAILURE,
            summary="git executable not found or unresponsive",
            checks=[Check("git-available", "git executable", "fail", "git was not found on PATH or did not respond")],
        )

    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return CommandResult(
            command="test",
            schema_version=SCHEMA_VERSION,
            generated_at=iso_now(clock),
            repo_root=None,
            exit_code=exit_codes.REPO_NOT_FOUND,
            summary=str(exc),
            checks=[Check("repo-discovery", "Repository discovery", "fail", str(exc))],
        )

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]

    try:
        config = load_config(repo_root)
    except ConfigError as exc:
        config = dict(DEFAULT_CONFIG_FALLBACK)
        checks.append(Check("config-validity", "Configuration validity", "warning", str(exc)))

    plan: TestPlan = build_full_test_plan(repo_root, config)

    if not plan.commands:
        checks.append(Check(
            "full-test-plan", "Full test plan", "warning",
            "no supported test command could be selected for a full run; see warnings",
        ))
    else:
        checks.append(Check(
            "full-test-plan", "Full test plan", "pass",
            f"{len(plan.commands)} full-suite command(s) selected",
        ))
    for warning in plan.warnings:
        checks.append(Check("full-test-plan-gap", "Full test plan gap", "warning", warning))

    data: dict = {"plan": plan.to_dict()}

    do_execute = not (plan_only or dry_run)

    if not do_execute:
        exit_code = exit_codes.WARNINGS_PRESENT if plan.warnings else exit_codes.SUCCESS
        mode = "plan" if plan_only else "dry-run"
        summary = f"full {mode}: {len(plan.commands)} command(s) (exit {exit_code})"
        result = CommandResult(
            command="test", schema_version=SCHEMA_VERSION, generated_at=iso_now(clock),
            repo_root=str(repo_root), exit_code=exit_code, summary=summary, checks=checks, data=data,
        )
        if write_log:
            LogWriter(repo_root, "test", clock=clock).write("test-full-plan.log", _render_plan_log_text(result))
        return result

    log_writer = LogWriter(repo_root, "test", clock=clock) if write_log else None
    execution: ExecutionSummary = execute_plan(repo_root, plan, log_writer)

    data["execution"] = [
        {
            "command": r.command, "cwd": r.cwd, "log_name": r.log_name, "log_path": r.log_path,
            "returncode": r.returncode, "duration_seconds": r.duration_seconds,
            "timed_out": r.timed_out, "error": r.error, "ran": r.ran, "succeeded": r.succeeded,
        }
        for r in execution.results
    ]

    for r in execution.results:
        label = " ".join(r.command)
        if r.succeeded:
            checks.append(Check("test-execution", f"ran: {label}", "pass", f"exit {r.returncode} in {r.duration_seconds}s"))
        elif r.timed_out:
            checks.append(Check("test-execution", f"ran: {label}", "fail", f"timed out after {r.duration_seconds}s"))
        elif r.error is not None:
            checks.append(Check("test-execution", f"ran: {label}", "fail", r.error))
        else:
            checks.append(Check("test-execution", f"ran: {label}", "fail", f"exit {r.returncode} in {r.duration_seconds}s"))

    exit_code = _compute_execution_exit_code(execution, plan)
    summary = (
        f"full: {len(execution.results)} command(s) run, "
        f"{sum(1 for r in execution.results if r.succeeded)} succeeded (exit {exit_code})"
    )

    result = CommandResult(
        command="test", schema_version=SCHEMA_VERSION, generated_at=iso_now(clock),
        repo_root=str(repo_root), exit_code=exit_code, summary=summary, checks=checks, data=data,
    )

    if write_log:
        LogWriter(repo_root, "test", clock=clock).write("test-full-summary.log", _render_execution_log_text(result))

    return result


def _compute_execution_exit_code(execution: ExecutionSummary, plan: TestPlan) -> int:
    if execution.results and execution.any_failed:
        return exit_codes.COMMAND_EXECUTION_FAILURE
    if plan.warnings:
        return exit_codes.WARNINGS_PRESENT
    return exit_codes.SUCCESS


def _render_plan_log_text(result: CommandResult) -> str:
    lines = [f"forgeops test --targeted --plan - {result.generated_at}", f"repo_root: {result.repo_root}", ""]
    lines += [f"[{c.status}] {c.id}: {c.message}" for c in result.checks]
    return "\n".join(lines)


def _render_execution_log_text(result: CommandResult) -> str:
    lines = [f"forgeops test --targeted - {result.generated_at}", f"repo_root: {result.repo_root}", ""]
    lines += [f"[{c.status}] {c.id}: {c.message}" for c in result.checks]
    lines.append("")
    lines.append(result.summary)
    return "\n".join(lines)


def render_human(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    plan = result.data.get("plan", {})
    lines = ["forgeops test", f"summary: {result.summary}", ""]

    if plan.get("changed_files"):
        lines.append(f"changed files: {len(plan['changed_files'])}")
    if plan.get("commands"):
        lines.append("plan:")
        for c in plan["commands"]:
            lines.append(f"  [{c['scope']}/{c['confidence']}] {' '.join(c['command'])}  (cwd={c['cwd']})")
            lines.append(f"      reason: {c['reason']}")
        lines.append("")

    execution = result.data.get("execution")
    if execution:
        lines.append("execution:")
        for r in execution:
            status = "OK" if r["succeeded"] else "FAIL"
            lines.append(f"  [{status}] {' '.join(r['command'])} (cwd={r['cwd']}) - {r['duration_seconds']}s")
            if r["log_path"]:
                lines.append(f"      log: {r['log_path']}")
        lines.append("")

    lines.extend(render_checks(result.checks))
    return "\n".join(lines)
