"""`forgeops checkpoint` - writes a deterministic, resumable project-state
snapshot to `.agent/CURRENT_STATE.json` (the existing canonical
checkpoint location - see `docs/checkpoint-and-handoff.md`). Read-only
except for that one file: never touches TrendForge, never installs
anything, never invokes model reasoning, and never modifies source code
outside `.agent/`. Objective fields (branch, HEAD, working-tree shape,
detected stack, remote presence) are recomputed from git/the filesystem
every call; narrative fields a human or prior session wrote (mission,
completed_work, blockers, next_action, ...) are carried forward
unchanged - see `forgeops/state/checkpoint.py` for why."""
from __future__ import annotations

import json
from pathlib import Path

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


def run_checkpoint(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    dry_run: bool = False,
) -> CommandResult:
    if git_version() is None:
        return CommandResult(
            command="checkpoint",
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
            command="checkpoint",
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
        "checkpoint-data", "Checkpoint data", "pass",
        f"branch={document['branch']}, head={document['head']}, "
        f"staged={document['changed_files']['staged']}, "
        f"modified={document['changed_files']['modified']}, "
        f"untracked={document['changed_files']['untracked']}",
    ))

    rendered = json.dumps(document, indent=2, sort_keys=False) + "\n"

    if dry_run:
        checks.append(Check(
            "checkpoint-write", "Checkpoint write", "informational",
            f"dry-run: would write {state_path} ({len(rendered)} bytes) - no file was written",
        ))
    else:
        try:
            atomic_write_text(state_path, rendered)
            checks.append(Check("checkpoint-write", "Checkpoint write", "pass", f"wrote {state_path}"))
        except OSError as exc:
            checks.append(Check("checkpoint-write", "Checkpoint write", "fail", f"could not write {state_path}: {exc}"))

    exit_code = _compute_exit_code(checks)
    verb = "would write" if dry_run else "wrote"
    summary = f"checkpoint: {verb} {state_path} (exit {exit_code})"

    result = CommandResult(
        command="checkpoint",
        schema_version=SCHEMA_VERSION,
        generated_at=iso_now(clock),
        repo_root=str(repo_root),
        exit_code=exit_code,
        summary=summary,
        checks=checks,
        data={"checkpoint": document, "state_path": str(state_path), "dry_run": dry_run},
    )

    if write_log:
        LogWriter(repo_root, "checkpoint", clock=clock).write("checkpoint.log", _render_log_text(result))

    return result


def _compute_exit_code(checks: list[Check]) -> int:
    statuses = {c.status for c in checks}
    if "fail" in statuses:
        return exit_codes.COMMAND_EXECUTION_FAILURE
    if "warning" in statuses:
        return exit_codes.WARNINGS_PRESENT
    return exit_codes.SUCCESS


def _render_log_text(result: CommandResult) -> str:
    lines = [f"forgeops checkpoint - {result.generated_at}", f"repo_root: {result.repo_root}", ""]
    lines += [f"[{c.status}] {c.id}: {c.message}" for c in result.checks]
    lines.append("")
    lines.append(result.summary)
    return "\n".join(lines)


def render_human(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops checkpoint", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    return "\n".join(lines)
