"""`forgeops agent register|list|show` - persistent, declarative agent
identity. Stores only identity metadata (kind, display name, status,
capabilities-as-declared-strings, current task assignment) under
`.agent/agents/AGENT_REGISTRY.json` - never a credential, environment
value, prompt, or session identifier, and never anything inferred by
probing an installed CLI or the local machine. See docs/agents.md for
the registry contract and explicit non-goals (no execution, no session
launching, no authentication, no MCP).

Follows the same shape as `forgeops/cli/task.py`: three `run_*` entry
points sharing one `render_human`, dispatching on `result.command`.
`agent register` is the only mutating command (supports `--dry-run`,
no `--confirm` needed - additive and reversible only in the sense that
nothing else references a freshly-registered, unassigned agent yet);
`agent list`/`agent show` are read-only. Reuses `forgeops.cli.task`'s
`_repo_level_block` gate (protected-reference-repo + initialized-project
checks) rather than a second copy of the same two checks."""
from __future__ import annotations

from pathlib import Path

from forgeops.core import exit_codes
from forgeops.core.git import git_version
from forgeops.core.paths import RepoNotFoundError, resolve_repo_root
from forgeops.core.result import Check, CommandResult
from forgeops.core.timestamps import Clock, iso_now
from forgeops.reporting.logs import LogWriter
from forgeops.state.agent_register import apply_agent_register, build_agent_register_plan
from forgeops.state.agent_registry import AGENT_REGISTRY_RELATIVE_PATH, load_agent_registry
from forgeops.cli.task import _repo_level_block

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


# --- agent register -----------------------------------------------------------


def run_agent_register(
    agent_id: str,
    kind: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    dry_run: bool = False,
    display_name: str | None = None,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("agent-register", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("agent-register", exc, clock)

    blocked = _repo_level_block(repo_root, "agent-register", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    plan = build_agent_register_plan(repo_root, agent_id, kind, display_name)

    if plan.conflicts:
        for c in plan.conflicts:
            checks.append(Check(f"preflight-{c.key}", f"Preflight: {c.key}", "blocked", c.message))
    else:
        checks.append(Check("preflight", "Preflight", "pass", "no conflicts detected"))

    data: dict = {
        "dry_run": dry_run,
        "agent_id": agent_id,
        "kind": kind,
        "display_name": plan.display_name,
        "conflicts": [{"key": c.key, "message": c.message} for c in plan.conflicts],
        "would_proceed": not plan.has_conflict,
    }

    if plan.has_conflict:
        data["action"] = "blocked"
        exit_code = exit_codes.BLOCKED
        summary = f"agent register: blocked by {len(plan.conflicts)} conflict(s) (exit {exit_code})"
        return _finish(repo_root, "agent-register", checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        data["action"] = "would_register"
        checks.append(Check(
            "write", "Agent registration", "informational",
            f"dry-run: would register agent '{agent_id}' (kind={kind}) with status 'registered' - no file was written",
        ))
        exit_code = exit_codes.SUCCESS
        summary = f"agent register: dry-run, would register {agent_id} (exit {exit_code})"
        return _finish(repo_root, "agent-register", checks, data, exit_code, summary, clock, write_log)

    outcome = apply_agent_register(repo_root, plan, clock)
    if not outcome.ok:
        data["action"] = "registration_failed"
        data["partial_state"] = outcome.partial_state
        data["manual_recovery_recommendation"] = (
            "Inspect `.agent/agents/AGENT_REGISTRY.json` directly. forgeops does not retry or "
            "force-replace automatically; resolve manually before retrying."
        )
        checks.append(Check("write", "Agent registration", "fail", f"registration did not complete: {outcome.partial_state}"))
        exit_code = exit_codes.COMMAND_EXECUTION_FAILURE
        summary = f"agent register: registration failed for {agent_id} (exit {exit_code})"
        return _finish(repo_root, "agent-register", checks, data, exit_code, summary, clock, write_log)

    data["action"] = "registered"
    checks.append(Check("write", "Agent registration", "pass", f"registered agent '{agent_id}' (kind={kind})"))
    exit_code = exit_codes.SUCCESS
    summary = f"agent register: registered {agent_id} (exit {exit_code})"
    return _finish(repo_root, "agent-register", checks, data, exit_code, summary, clock, write_log)


# --- agent list -----------------------------------------------------------------


def run_agent_list(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    kind_filter: str | None = None,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("agent-list", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("agent-list", exc, clock)

    blocked = _repo_level_block(repo_root, "agent-list", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    registry = load_agent_registry(repo_root)
    if registry.warning:
        checks.append(Check("agent-registry-schema", "Agent registry schema", "warning", registry.warning))

    records = list(registry.records) if registry.warning is None else []
    if kind_filter:
        records = [r for r in records if r.kind == kind_filter]

    data = {
        "kind_filter": kind_filter,
        "agents": [r.to_dict() for r in records],
        "total": len(records),
    }
    exit_code = exit_codes.WARNINGS_PRESENT if registry.warning else exit_codes.SUCCESS
    summary = f"agent list: {len(records)} agent(s) (exit {exit_code})"
    return _finish(repo_root, "agent-list", checks, data, exit_code, summary, clock, write_log)


# --- agent show ---------------------------------------------------------------


def run_agent_show(
    agent_id: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("agent-show", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("agent-show", exc, clock)

    blocked = _repo_level_block(repo_root, "agent-show", clock, write_log)
    if blocked is not None:
        return blocked

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    registry = load_agent_registry(repo_root)
    if registry.warning is not None:
        checks.append(Check("agent-lookup", "Lookup: agent-registry-malformed", "blocked", registry.warning))
        exit_code = exit_codes.BLOCKED
        data = {"found": False, "agent_id": agent_id}
        summary = f"agent show: registry could not be read (exit {exit_code})"
        return _finish(repo_root, "agent-show", checks, data, exit_code, summary, clock, write_log)

    matches = [r for r in registry.records if r.agent_id == agent_id]
    if not matches:
        checks.append(Check("agent-lookup", "Lookup: agent-not-found", "blocked", f"no agent '{agent_id}' found in {AGENT_REGISTRY_RELATIVE_PATH}"))
        exit_code = exit_codes.BLOCKED
        data = {"found": False, "agent_id": agent_id}
        summary = f"agent show: '{agent_id}' not found (exit {exit_code})"
        return _finish(repo_root, "agent-show", checks, data, exit_code, summary, clock, write_log)

    record = matches[0]
    checks.append(Check("agent-lookup", "Agent lookup", "pass", f"found agent '{agent_id}'"))
    data = {"found": True, "agent_id": agent_id, "agent": record.to_dict()}
    exit_code = exit_codes.SUCCESS
    summary = f"agent show: {agent_id} ({record.status}) (exit {exit_code})"
    return _finish(repo_root, "agent-show", checks, data, exit_code, summary, clock, write_log)


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
    if result.command == "agent-register":
        return _render_human_register(result)
    if result.command == "agent-list":
        return _render_human_list(result)
    return _render_human_show(result)


def _render_human_register(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops agent register", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    d = result.data
    lines.append("")
    lines.append(f"agent id: {d.get('agent_id')}")
    lines.append(f"kind: {d.get('kind')}")
    lines.append(f"display name: {d.get('display_name')}")
    lines.append(f"would proceed: {d.get('would_proceed')}")
    if d.get("conflicts"):
        lines.append("conflicts:")
        for c in d["conflicts"]:
            lines.append(f"  - {c['key']}: {c['message']}")
    if d.get("partial_state") is not None:
        lines.append(f"partial state after failure: {d['partial_state']}")
        lines.append(f"recovery: {d.get('manual_recovery_recommendation')}")
    return "\n".join(lines)


def _render_human_list(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops agent list", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    d = result.data
    if d.get("agents"):
        lines.append("")
        for a in d["agents"]:
            lines.append(
                f"- {a['agent_id']}: {a['display_name']} [{a['kind']}] status={a['status']} "
                f"assigned_task={a['assigned_task_id']} updated={a['updated_at']}"
            )
    return "\n".join(lines)


def _render_human_show(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops agent show", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    d = result.data
    lines.append("")
    if not d.get("found"):
        lines.append(f"agent: {d.get('agent_id')} - not found")
        return "\n".join(lines)
    a = d["agent"]
    lines.append(f"agent: {a['agent_id']} - {a['display_name']}")
    lines.append(f"kind: {a['kind']}")
    lines.append(f"status: {a['status']}")
    lines.append(f"capabilities: {a['capabilities']}")
    lines.append(f"assigned task: {a['assigned_task_id']}")
    lines.append(f"created_at: {a['created_at']}  updated_at: {a['updated_at']}")
    return "\n".join(lines)
