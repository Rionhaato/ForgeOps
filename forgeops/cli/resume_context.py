"""`forgeops resume-context` - read-only, compact, bounded-size summary a
brand-new Claude/Codex session can consume instead of rereading
`.agent/CURRENT_STATE.json`, `.agent/HANDOFF.md`, or the repository
itself. See `docs/context-efficiency.md` for the design rationale and
`forgeops.state.resume_context` for the pure document builder.

Never writes anything - this command exists purely to reduce how much a
new session must read, not to add another state file to keep in sync."""
from __future__ import annotations

from pathlib import Path

from forgeops.core import exit_codes
from forgeops.core.git import git_version
from forgeops.core.paths import RepoNotFoundError, resolve_repo_root
from forgeops.core.result import Check, CommandResult
from forgeops.core.timestamps import Clock, iso_now
from forgeops.state.resume_context import RESUME_CONTEXT_MAX_BYTES, build_resume_context

SCHEMA_VERSION = 1


def run_resume_context(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
) -> CommandResult:
    if git_version() is None:
        return CommandResult(
            command="resume-context",
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
            command="resume-context",
            schema_version=SCHEMA_VERSION,
            generated_at=iso_now(clock),
            repo_root=None,
            exit_code=exit_codes.REPO_NOT_FOUND,
            summary=str(exc),
            checks=[Check("repo-discovery", "Repository discovery", "fail", str(exc))],
        )

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]

    document = build_resume_context(repo_root, clock)

    if document.get("state_warning"):
        checks.append(Check("state-schema", "Previous state schema", "warning", document["state_warning"]))
    if document["state_paths"]["current_state"] is None:
        checks.append(Check(
            "state-missing", "CURRENT_STATE.json", "warning",
            "no .agent/CURRENT_STATE.json found - run `forgeops checkpoint` to establish one",
        ))

    import json as _json
    size_bytes = len(_json.dumps(document, indent=2).encode("utf-8"))
    checks.append(Check(
        "output-size", "Output size ceiling", "pass" if size_bytes <= RESUME_CONTEXT_MAX_BYTES else "warning",
        f"{size_bytes} bytes (ceiling: {RESUME_CONTEXT_MAX_BYTES})",
    ))

    exit_code = _compute_exit_code(checks)
    summary = f"resume-context: phase={document.get('phase') or '(none)'} , {size_bytes} bytes (exit {exit_code})"

    return CommandResult(
        command="resume-context",
        schema_version=SCHEMA_VERSION,
        generated_at=iso_now(clock),
        repo_root=str(repo_root),
        exit_code=exit_code,
        summary=summary,
        checks=checks,
        data=document,
    )


def _compute_exit_code(checks: list[Check]) -> int:
    statuses = {c.status for c in checks}
    if "fail" in statuses:
        return exit_codes.COMMAND_EXECUTION_FAILURE
    if "warning" in statuses:
        return exit_codes.WARNINGS_PRESENT
    return exit_codes.SUCCESS


def render_human(result: CommandResult) -> str:
    d = result.data
    lines = ["forgeops resume-context", f"summary: {result.summary}", ""]
    if not d:
        from forgeops.cli.render import render_checks
        lines.extend(render_checks(result.checks))
        return "\n".join(lines)

    repo = d.get("repository") or {}
    lines.append(f"repository: {repo.get('name') or '(unnamed)'} ({repo.get('root')})")
    lines.append(f"branch: {d.get('branch')}  head: {d.get('head')}")
    wt = d.get("working_tree") or {}
    lines.append(
        f"working tree: {'clean' if wt.get('clean') else 'dirty'} "
        f"(staged={wt.get('staged')}, modified={wt.get('modified')}, untracked={wt.get('untracked')})"
    )
    lines.append(f"phase: {d.get('phase') or '(none recorded)'}")

    lv = d.get("latest_validation")
    lines.append(f"latest validation: {lv['command']} -> {lv['result']} ({lv['timestamp_local']})" if lv else "latest validation: (none recorded)")

    fip = d.get("files_in_progress") or {}
    for group in ("modified", "untracked"):
        g = fip.get(group) or {}
        items = g.get("items") or []
        if items:
            suffix = f" (+{g['total'] - len(items)} more)" if g.get("truncated") else ""
            lines.append(f"{group} files: {', '.join(items)}{suffix}")

    blockers = d.get("unresolved_blockers") or []
    total_blockers = d.get("unresolved_blockers_total", len(blockers))
    if blockers:
        lines.append(f"unresolved blockers ({total_blockers} total):")
        lines.extend(f"  - [{b['severity']}] {b['description']}" for b in blockers)
    else:
        lines.append("unresolved blockers: (none recorded)")

    if d.get("next_action"):
        lines.append(f"next action: {d['next_action']}")

    do_not_repeat = d.get("do_not_repeat") or []
    if do_not_repeat:
        lines.append("do not repeat (already validated):")
        lines.extend(f"  - {item}" for item in do_not_repeat)

    lines.append(f"stop boundary: {d.get('stop_boundary')}")
    lines.append(f"approval boundaries: {', '.join(d.get('approval_boundaries') or [])}")

    sp = d.get("state_paths") or {}
    lines.append(f"state paths: current_state={sp.get('current_state')}, handoff={sp.get('handoff')}")

    from forgeops.cli.render import render_checks
    lines.append("")
    lines.extend(render_checks(result.checks))
    return "\n".join(lines)
