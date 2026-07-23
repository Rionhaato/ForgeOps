"""`forgeops cleanup` - conservative, defaults-to-dry-run cleanup of
ForgeOps-managed development processes and stale runtime records.
Mutating (unlike every other command in this package) - see
`docs/process-list-and-cleanup.md` for the full safety model.

Hard invariants, enforced by construction rather than by convention:
- Only a process classified `managed` by `process_association.classify_process`
  (i.e. an exact registry PID + repository + start-time match, with an
  allowed category) is ever a termination candidate. `associated`,
  `uncertain`, and `unrelated` processes are never touched no matter how
  much heuristic evidence accumulates.
- Real termination requires `execute=True`; the default is always a dry
  run that touches nothing.
- Every candidate is revalidated (PID still exists, start time still
  matches) immediately before termination is attempted - closing the gap
  between an earlier `process-list` snapshot and the moment cleanup acts.
- Termination is `taskkill /PID <pid>` only - no `/F` (force). If the
  process is still running after a bounded wait, cleanup reports failure
  and stops; it never escalates."""
from __future__ import annotations

import time
from pathlib import Path

from forgeops.core import exit_codes
from forgeops.core.git import git_version
from forgeops.core.paths import RepoNotFoundError, resolve_repo_root
from forgeops.core.result import Check, CommandResult
from forgeops.core.subprocess_utils import run as run_subprocess
from forgeops.core.timestamps import Clock, iso_now
from forgeops.detectors.process_association import classify_process
from forgeops.detectors.processes import (
    ProcessInfo,
    get_process_start_time_utc,
    list_os_processes,
    process_exists,
)
from forgeops.reporting.logs import LogWriter
from forgeops.state.runtime_registry import RegistryDocument, load_registry, save_registry

SCHEMA_VERSION = 1
DEFAULT_GRACEFUL_WAIT_SECONDS = 5.0
GRACEFUL_POLL_INTERVAL_SECONDS = 0.5
TASKKILL_TIMEOUT_SECONDS = 10.0


def run_cleanup(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    execute: bool = False,
    graceful_wait_seconds: float = DEFAULT_GRACEFUL_WAIT_SECONDS,
) -> CommandResult:
    if git_version() is None:
        return CommandResult(
            command="cleanup",
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
            command="cleanup",
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
    live_pids = set(all_by_pid.keys())

    termination_candidates: list[dict] = []
    stale_record_candidates: list[dict] = []
    surviving_records = list(registry.records)

    for record in registry.records:
        proc = all_by_pid.get(record.pid)
        if proc is None:
            stale_record_candidates.append({
                "pid": record.pid,
                "category": record.category,
                "reason": "no live process with this PID (process has exited)",
            })
            continue

        result = classify_process(proc, repo_root, registry, all_by_pid)
        if result.classification == "stale_record":
            stale_record_candidates.append({
                "pid": record.pid,
                "category": record.category,
                "reason": result.ineligible_reason or "stale record",
            })
            continue
        if result.classification == "managed" and result.cleanup_eligible:
            termination_candidates.append({
                "pid": record.pid,
                "category": record.category,
                "port": record.port,
                "eligible": True,
                "eligibility_reason": "registry PID + repository + start_time all match; category is an allowed managed category",
            })

    action_log: list[dict] = []

    for candidate in termination_candidates:
        pid = candidate["pid"]
        if not execute:
            action_log.append({
                **candidate,
                "planned_action": "graceful_terminate",
                "dry_run_outcome": "would attempt graceful termination (taskkill /PID, no /F) - no action taken (dry-run)",
                "terminated": False,
            })
            continue

        # Revalidate immediately before acting - closes the gap between
        # this run's own discovery snapshot and the moment we act.
        record = next(r for r in registry.records if r.pid == pid)
        if not process_exists(pid):
            action_log.append({
                **candidate,
                "planned_action": "graceful_terminate",
                "dry_run_outcome": "skipped: process no longer exists at revalidation time",
                "terminated": False,
            })
            surviving_records = [r for r in surviving_records if r.pid != pid]
            continue

        fresh_start_time = get_process_start_time_utc(pid)
        if fresh_start_time != record.start_time_utc:
            action_log.append({
                **candidate,
                "planned_action": "graceful_terminate",
                "dry_run_outcome": "skipped: PID reuse detected at revalidation time (start time changed) - never terminated",
                "terminated": False,
            })
            continue

        proc = run_subprocess(["taskkill.exe", "/PID", str(pid)], timeout=TASKKILL_TIMEOUT_SECONDS)
        deadline = time.monotonic() + graceful_wait_seconds
        still_running = True
        while time.monotonic() < deadline:
            if not process_exists(pid):
                still_running = False
                break
            time.sleep(GRACEFUL_POLL_INTERVAL_SECONDS)
        if not still_running:
            still_running = process_exists(pid)

        if still_running:
            action_log.append({
                **candidate,
                "planned_action": "graceful_terminate",
                "dry_run_outcome": (
                    f"graceful termination did not stop the process within {graceful_wait_seconds}s "
                    f"(taskkill result: {'ok' if proc.ok else proc.error or f'exit {proc.returncode}'}); "
                    "not escalating to force-kill"
                ),
                "terminated": False,
            })
        else:
            action_log.append({
                **candidate,
                "planned_action": "graceful_terminate",
                "dry_run_outcome": "gracefully terminated",
                "terminated": True,
            })
            surviving_records = [r for r in surviving_records if r.pid != pid]

    for candidate in stale_record_candidates:
        if not execute:
            action_log.append({
                **candidate,
                "planned_action": "remove_stale_record",
                "dry_run_outcome": "would remove stale registry record - no action taken (dry-run)",
                "terminated": False,
            })
            continue
        surviving_records = [r for r in surviving_records if r.pid != candidate["pid"]]
        action_log.append({
            **candidate,
            "planned_action": "remove_stale_record",
            "dry_run_outcome": "removed stale registry record",
            "terminated": False,
        })

    registry_changed = execute and len(surviving_records) != len(registry.records)
    if registry_changed:
        try:
            save_registry(repo_root, RegistryDocument(records=surviving_records))
            checks.append(Check("registry-write", "Process registry write", "pass", "updated after cleanup"))
        except OSError as exc:
            checks.append(Check("registry-write", "Process registry write", "fail", f"could not update registry: {exc}"))

    for entry in action_log:
        status = "pass" if entry.get("terminated") or entry["planned_action"] == "remove_stale_record" and execute else "informational"
        checks.append(Check(
            f"cleanup.{entry['pid']}",
            f"PID {entry['pid']} ({entry.get('category', '?')})",
            status,
            entry["dry_run_outcome"],
        ))

    if not termination_candidates and not stale_record_candidates:
        checks.append(Check("cleanup-candidates", "Cleanup candidates", "pass", "no eligible processes or stale records found"))

    exit_code = _compute_exit_code(discovery.limitation, registry.warning, action_log, checks)
    mode = "execute" if execute else "dry-run"
    summary = (
        f"cleanup ({mode}): {len(termination_candidates)} termination candidate(s), "
        f"{len(stale_record_candidates)} stale record(s) (exit {exit_code})"
    )

    result = CommandResult(
        command="cleanup",
        schema_version=SCHEMA_VERSION,
        generated_at=iso_now(clock),
        repo_root=str(repo_root),
        exit_code=exit_code,
        summary=summary,
        checks=checks,
        data={
            "execute": execute,
            "termination_candidates": termination_candidates,
            "stale_record_candidates": stale_record_candidates,
            "actions": action_log,
        },
    )

    if write_log:
        LogWriter(repo_root, "cleanup", clock=clock).write("cleanup.log", _render_log_text(result))

    return result


def _compute_exit_code(
    discovery_limitation: str | None,
    registry_warning: str | None,
    action_log: list[dict],
    checks: list[Check],
) -> int:
    if any(c.status == "fail" for c in checks):
        return exit_codes.COMMAND_EXECUTION_FAILURE
    if discovery_limitation or registry_warning:
        return exit_codes.WARNINGS_PRESENT
    if any("did not stop the process" in a["dry_run_outcome"] for a in action_log):
        return exit_codes.WARNINGS_PRESENT
    if action_log:
        return exit_codes.WARNINGS_PRESENT
    return exit_codes.SUCCESS


def _render_log_text(result: CommandResult) -> str:
    lines = [f"forgeops cleanup - {result.generated_at}", f"repo_root: {result.repo_root}", ""]
    lines += [f"[{c.status}] {c.id}: {c.message}" for c in result.checks]
    lines.append("")
    lines.append(result.summary)
    return "\n".join(lines)


def render_human(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops cleanup", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    return "\n".join(lines)
