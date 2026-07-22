"""Executes a TestPlan's commands sequentially with bounded timeouts,
writing sanitized raw output to disk. Policy: every selected command is
attempted regardless of earlier failures (a partial run must never hide
which specific commands passed or failed), and "ran" is always True for
an attempted command - a missing executable or a timeout is recorded as
a real failure on that result, never silently skipped."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from forgeops.core.subprocess_utils import run
from forgeops.reporting.logs import LogWriter
from forgeops.testing.planner import TestCommand, TestPlan

DEFAULT_TIMEOUT_SECONDS = 600.0


@dataclass(frozen=True)
class CommandExecutionResult:
    command: list[str]
    cwd: str
    log_name: str
    log_path: str | None
    returncode: int | None
    duration_seconds: float
    timed_out: bool
    error: str | None
    ran: bool

    @property
    def succeeded(self) -> bool:
        return self.ran and not self.timed_out and self.error is None and self.returncode == 0


@dataclass
class ExecutionSummary:
    results: list[CommandExecutionResult] = field(default_factory=list)

    @property
    def all_succeeded(self) -> bool:
        return all(r.succeeded for r in self.results)

    @property
    def any_failed(self) -> bool:
        return any(not r.succeeded for r in self.results)


def execute_plan(
    repo_root: Path,
    plan: TestPlan,
    log_writer: LogWriter | None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ExecutionSummary:
    results: list[CommandExecutionResult] = []
    for command in plan.commands:
        results.append(_execute_one(repo_root, command, log_writer, timeout))
    return ExecutionSummary(results=results)


def _execute_one(
    repo_root: Path,
    command: TestCommand,
    log_writer: LogWriter | None,
    timeout: float,
) -> CommandExecutionResult:
    cwd_path = repo_root if command.cwd == "." else repo_root / command.cwd
    start = time.monotonic()
    proc_result = run(command.command, cwd=cwd_path, timeout=timeout)
    duration = time.monotonic() - start

    raw_output = (
        f"$ {' '.join(command.command)}\n"
        f"cwd: {cwd_path}\n"
        f"reason: {command.reason}\n"
        f"duration: {duration:.3f}s\n\n"
        f"--- stdout ---\n{proc_result.stdout}\n"
        f"--- stderr ---\n{proc_result.stderr}\n"
    )
    log_path = log_writer.write(command.log_name, raw_output) if log_writer is not None else None

    return CommandExecutionResult(
        command=command.command,
        cwd=command.cwd,
        log_name=command.log_name,
        log_path=str(log_path) if log_path else None,
        returncode=proc_result.returncode,
        duration_seconds=round(duration, 3),
        timed_out=proc_result.timed_out,
        error=proc_result.error,
        ran=True,
    )
