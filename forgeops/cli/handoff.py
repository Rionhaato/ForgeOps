"""`forgeops handoff` - writes a concise, resumable markdown summary to
`.agent/HANDOFF.md` (the existing canonical location) so another Claude
Code or Codex session can safely continue. Derived entirely from the
same deterministic data `forgeops checkpoint` computes
(`forgeops.state.checkpoint.build_checkpoint_data`) plus a small set of
static, well-known constants (standard validation commands, approval
boundaries) mirrored from this repository's own `CLAUDE.md` - never from
free-form model reasoning. See `docs/checkpoint-and-handoff.md` for the
consistency model between this command and `checkpoint`."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from forgeops.core import exit_codes
from forgeops.core.git import git_version
from forgeops.core.paths import RepoNotFoundError, resolve_repo_root
from forgeops.core.result import Check, CommandResult
from forgeops.core.timestamps import Clock, iso_now
from forgeops.reporting.logs import LogWriter
from forgeops.state.atomic_write import atomic_write_text
from forgeops.state.checkpoint import build_checkpoint_data, load_previous_state

SCHEMA_VERSION = 1
STATE_RELATIVE_PATH = Path(".agent") / "CURRENT_STATE.json"
HANDOFF_RELATIVE_PATH = Path(".agent") / "HANDOFF.md"

# Mirrors CLAUDE.md section 8 ("Standard validation commands") - update
# both places together if the standard validation sequence changes.
STANDARD_VALIDATION_COMMANDS = (
    "python -m pytest tests -q",
    "python -m compileall -q forgeops tests",
    "python -m forgeops doctor",
    "python -m forgeops status",
    "python -m forgeops audit",
    "git diff --check",
    "git status --short",
)

# Mirrors CLAUDE.md section 11 ("Approval boundaries") and this repo's
# own established .agent/HANDOFF.md wording for exactly this list -
# never invented fresh per invocation.
APPROVAL_BOUNDARY_CATEGORIES = (
    "installation",
    "authentication",
    "secrets access",
    "destructive actions",
    "production/publishing/spending/deployment/financial actions",
    "commits (unless explicitly authorized for this checkpoint)",
    "genuine ambiguity about sensitive files",
)

PROHIBITED_ACTIONS = (
    "Do not push, deploy, publish, or configure a remote.",
    "Do not force-push, `git reset --hard`, or rewrite history without explicit operator approval.",
    "Do not amend, squash, or rebase an existing commit.",
    "Do not install dependencies or authenticate an external service.",
    "Do not invoke any command against the read-only reference repository, if one is configured (see below).",
)


def run_handoff(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    dry_run: bool = False,
) -> CommandResult:
    if git_version() is None:
        return CommandResult(
            command="handoff",
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
            command="handoff",
            schema_version=SCHEMA_VERSION,
            generated_at=iso_now(clock),
            repo_root=None,
            exit_code=exit_codes.REPO_NOT_FOUND,
            summary=str(exc),
            checks=[Check("repo-discovery", "Repository discovery", "fail", str(exc))],
        )

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]

    state_path = repo_root / STATE_RELATIVE_PATH
    previous = load_previous_state(state_path)
    if previous.warning:
        checks.append(Check("previous-state-schema", "Previous state schema", "warning", previous.warning))

    document = build_checkpoint_data(repo_root, previous, clock)
    checks.append(Check(
        "handoff-data", "Handoff data", "pass",
        f"derived from branch={document['branch']}, head={document['head']}",
    ))

    rendered_markdown = _render_markdown(document, generated_at=document["generated_at"])
    handoff_path = repo_root / HANDOFF_RELATIVE_PATH

    if dry_run:
        checks.append(Check(
            "handoff-write", "Handoff write", "informational",
            f"dry-run: would write {handoff_path} ({len(rendered_markdown)} bytes) - no file was written",
        ))
    else:
        try:
            atomic_write_text(handoff_path, rendered_markdown)
            checks.append(Check("handoff-write", "Handoff write", "pass", f"wrote {handoff_path}"))
        except OSError as exc:
            checks.append(Check("handoff-write", "Handoff write", "fail", f"could not write {handoff_path}: {exc}"))

    exit_code = _compute_exit_code(checks)
    verb = "would write" if dry_run else "wrote"
    summary = f"handoff: {verb} {handoff_path} (exit {exit_code})"

    result = CommandResult(
        command="handoff",
        schema_version=SCHEMA_VERSION,
        generated_at=iso_now(clock),
        repo_root=str(repo_root),
        exit_code=exit_code,
        summary=summary,
        checks=checks,
        data={"handoff_markdown": rendered_markdown, "handoff_path": str(handoff_path), "dry_run": dry_run},
    )

    if write_log:
        LogWriter(repo_root, "handoff", clock=clock).write("handoff.log", _render_log_text(result))

    return result


def _compute_exit_code(checks: list[Check]) -> int:
    statuses = {c.status for c in checks}
    if "fail" in statuses:
        return exit_codes.COMMAND_EXECUTION_FAILURE
    if "warning" in statuses:
        return exit_codes.WARNINGS_PRESENT
    return exit_codes.SUCCESS


def _render_markdown(document: dict[str, Any], generated_at: str) -> str:
    lines: list[str] = []
    lines.append("# Handoff (generated by `forgeops handoff`)")
    lines.append("")
    lines.append(f"Generated: {generated_at}")
    lines.append("")

    lines.append("## Repository")
    lines.append("")
    lines.append(f"- Branch: `{document['branch']}`")
    lines.append(f"- HEAD: `{document['head']}`")
    remote = document["remote"]
    if remote["configured"]:
        lines.append(f"- Remote: configured ({', '.join(remote['names'])})")
    else:
        lines.append("- Remote: not configured")
    lines.append("")

    reference_repo = (document.get("repository") or {}).get("reference_repo_readonly")
    if reference_repo:
        lines.append("## Read-only reference repository")
        lines.append("")
        lines.append(
            f"`{reference_repo}` is configured as a read-only reference repository: "
            "never modify it, never commit to it, never run a mutating command against it."
        )
        lines.append("")

    lines.append("## Current phase")
    lines.append("")
    last_checkpoint = document.get("last_checkpoint") or {}
    phase = last_checkpoint.get("phase") if isinstance(last_checkpoint, dict) else None
    lines.append(phase or "(no phase recorded yet - run `forgeops checkpoint` after establishing one)")
    lines.append("")

    lines.append("## Completed work")
    lines.append("")
    completed = document.get("completed_work") or []
    if completed:
        lines.extend(f"- {item}" for item in completed)
    else:
        lines.append("(none recorded)")
    lines.append("")

    lines.append("## Accepted validation evidence")
    lines.append("")
    recent_tests = document.get("recent_tests") or []
    if recent_tests:
        for t in recent_tests:
            if isinstance(t, dict):
                lines.append(f"- `{t.get('command', '?')}` -> {t.get('result', '?')} ({t.get('timestamp_local', '?')})")
    else:
        lines.append("(none recorded)")
    lines.append("")

    lines.append("## Current working-tree state")
    lines.append("")
    changed = document["changed_files"]
    lines.append(
        f"{changed['staged']} staged, {changed['modified']} modified, {changed['untracked']} untracked "
        f"(local changes present: {document['has_local_changes']})"
    )
    lines.append("")

    lines.append("## Detected stack")
    lines.append("")
    stack = document.get("detected_stack") or []
    if stack:
        for s in stack:
            flag = " (ambiguous)" if s.get("ambiguous") else ""
            lines.append(f"- {s['technology']} ({s['confidence']}{flag}, {s['evidence_count']} evidence item(s))")
    else:
        lines.append("(no stack detected)")
    lines.append("")

    lines.append("## Known limitations / blockers")
    lines.append("")
    blockers = document.get("blockers") or []
    if blockers:
        for b in blockers:
            if isinstance(b, dict):
                lines.append(f"- **{b.get('description', '?')}** (severity: {b.get('severity', '?')}) - {b.get('affects', '')}")
    else:
        lines.append("(none recorded)")
    lines.append("")

    lines.append("## Exact recommended next checkpoint")
    lines.append("")
    lines.append(document.get("next_action") or "(none recorded - establish one via forgeops checkpoint)")
    lines.append("")

    lines.append("## Commands the next agent should run first")
    lines.append("")
    lines.extend(f"- `{cmd}`" for cmd in STANDARD_VALIDATION_COMMANDS)
    lines.append("")

    lines.append("## Actions that remain prohibited without explicit approval")
    lines.append("")
    lines.extend(f"- {a}" for a in PROHIBITED_ACTIONS)
    lines.append("")
    lines.append("Explicit approval is required first for: " + ", ".join(APPROVAL_BOUNDARY_CATEGORIES) + ".")

    return "\n".join(lines) + "\n"


def _render_log_text(result: CommandResult) -> str:
    lines = [f"forgeops handoff - {result.generated_at}", f"repo_root: {result.repo_root}", ""]
    lines += [f"[{c.status}] {c.id}: {c.message}" for c in result.checks]
    lines.append("")
    lines.append(result.summary)
    return "\n".join(lines)


def render_human(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops handoff", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    return "\n".join(lines)
