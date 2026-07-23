"""`forgeops process-list` - read-only discovery of OS processes
associated with the target repository. Never installs anything, never
modifies or terminates a process, never requires model reasoning. See
`docs/process-list-and-cleanup.md` for the full association/
classification model shared with `forgeops cleanup`."""
from __future__ import annotations

from pathlib import Path

from forgeops.core import exit_codes
from forgeops.core.git import git_version
from forgeops.core.paths import RepoNotFoundError, resolve_repo_root
from forgeops.core.result import Check, CommandResult
from forgeops.core.timestamps import Clock, iso_now
from forgeops.detectors.process_association import classify_process
from forgeops.detectors.processes import ProcessInfo, list_os_processes
from forgeops.reporting.logs import LogWriter
from forgeops.security.redact import redact_text
from forgeops.state.runtime_registry import load_registry

SCHEMA_VERSION = 1
# Processes classified this way are not individually reported (they are
# the overwhelming majority on any real machine) - only their count is.
_UNREPORTED_CLASSIFICATIONS = ("unrelated",)


def run_process_list(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
) -> CommandResult:
    if git_version() is None:
        return CommandResult(
            command="process-list",
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
            command="process-list",
            schema_version=SCHEMA_VERSION,
            generated_at=iso_now(clock),
            repo_root=None,
            exit_code=exit_codes.REPO_NOT_FOUND,
            summary=str(exc),
            checks=[Check("repo-discovery", "Repository discovery", "fail", str(exc))],
        )

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]

    discovery = list_os_processes()
    if discovery.limitation:
        checks.append(Check("process-discovery-limitation", "Process discovery limitation", "warning", discovery.limitation))

    registry = load_registry(repo_root)
    if registry.warning:
        checks.append(Check("registry-schema", "Process registry schema", "warning", registry.warning))

    all_by_pid: dict[int, ProcessInfo] = {p.pid: p for p in discovery.processes}

    reported: list[dict] = []
    counts: dict[str, int] = {}
    for proc in discovery.processes:
        result = classify_process(proc, repo_root, registry, all_by_pid)
        counts[result.classification] = counts.get(result.classification, 0) + 1
        if result.classification in _UNREPORTED_CLASSIFICATIONS:
            continue
        reported.append({
            "pid": proc.pid,
            "ppid": proc.ppid,
            "name": proc.name,
            "command_summary": _summarize_command(proc.command_line),
            "working_directory": proc.working_directory,
            "start_time_utc": proc.start_time_utc,
            "listening_ports": list(proc.listening_ports),
            "classification": result.classification,
            "confidence": result.confidence,
            "evidence": result.evidence,
            "forgeops_managed": result.forgeops_managed,
            "cleanup_eligible": result.cleanup_eligible,
            "ineligible_reason": result.ineligible_reason,
        })

    for r in reported:
        checks.append(Check(
            f"process.{r['pid']}",
            f"PID {r['pid']} ({r['name']})",
            "pass" if r["classification"] == "managed" else "informational",
            f"{r['classification']} ({r['confidence']} confidence): {'; '.join(r['evidence']) or 'no evidence'}",
        ))

    data = {
        "platform_supported": discovery.platform_supported,
        "total_processes_scanned": len(discovery.processes),
        "counts_by_classification": counts,
        "processes": reported,
    }

    exit_code = _compute_exit_code(discovery.limitation, registry.warning)
    summary = (
        f"process-list: {len(discovery.processes)} scanned, {len(reported)} reported "
        f"(managed={counts.get('managed', 0)}, associated={counts.get('associated', 0)}, "
        f"uncertain={counts.get('uncertain', 0)}, stale_record={counts.get('stale_record', 0)}) (exit {exit_code})"
    )

    result_obj = CommandResult(
        command="process-list",
        schema_version=SCHEMA_VERSION,
        generated_at=iso_now(clock),
        repo_root=str(repo_root),
        exit_code=exit_code,
        summary=summary,
        checks=checks,
        data=data,
    )

    if write_log:
        LogWriter(repo_root, "process-list", clock=clock).write("process-list.log", _render_log_text(result_obj))

    return result_obj


def _summarize_command(command_line: str | None) -> str | None:
    """Bounded-length, redacted summary. Redaction is applied again here
    (defense in depth) even though `list_os_processes()` already redacts
    raw command lines at the discovery layer - this command's own
    reporting layer must never be the thing that lets a secret-shaped
    value slip through, regardless of how reliable the layer beneath it
    is or ever will be."""
    if command_line is None:
        return None
    redacted = redact_text(command_line)
    if len(redacted) <= 160:
        return redacted
    return redacted[:157] + "..."


def _compute_exit_code(discovery_limitation: str | None, registry_warning: str | None) -> int:
    if discovery_limitation or registry_warning:
        return exit_codes.WARNINGS_PRESENT
    return exit_codes.SUCCESS


def _render_log_text(result: CommandResult) -> str:
    lines = [f"forgeops process-list - {result.generated_at}", f"repo_root: {result.repo_root}", ""]
    lines += [f"[{c.status}] {c.id}: {c.message}" for c in result.checks]
    lines.append("")
    lines.append(result.summary)
    return "\n".join(lines)


def render_human(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops process-list", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    return "\n".join(lines)
