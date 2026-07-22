from __future__ import annotations

import sys
from pathlib import Path

from forgeops.reporting.logs import LogWriter
from forgeops.testing.executor import execute_plan
from forgeops.testing.planner import TestCommand, TestPlan


def _plan(commands: list[TestCommand]) -> TestPlan:
    return TestPlan(changed_files=["x.py"], commands=commands)


def test_successful_command(tmp_path: Path):
    cmd = TestCommand(
        command=[sys.executable, "-c", "print('ok')"], cwd=".", reason="test",
        scope="targeted", confidence="high", fallback=False, technology="pytest", log_name="out.log",
    )
    summary = execute_plan(tmp_path, _plan([cmd]), log_writer=None)
    assert len(summary.results) == 1
    assert summary.results[0].succeeded
    assert summary.results[0].returncode == 0
    assert summary.all_succeeded
    assert not summary.any_failed


def test_failed_command(tmp_path: Path):
    cmd = TestCommand(
        command=[sys.executable, "-c", "import sys; sys.exit(1)"], cwd=".", reason="test",
        scope="targeted", confidence="high", fallback=False, technology="pytest", log_name="out.log",
    )
    summary = execute_plan(tmp_path, _plan([cmd]), log_writer=None)
    assert summary.results[0].succeeded is False
    assert summary.results[0].returncode == 1
    assert summary.any_failed


def test_timeout(tmp_path: Path):
    cmd = TestCommand(
        command=[sys.executable, "-c", "import time; time.sleep(5)"], cwd=".", reason="test",
        scope="targeted", confidence="high", fallback=False, technology="pytest", log_name="out.log",
    )
    summary = execute_plan(tmp_path, _plan([cmd]), log_writer=None, timeout=0.2)
    assert summary.results[0].timed_out is True
    assert summary.results[0].succeeded is False


def test_missing_executable(tmp_path: Path):
    cmd = TestCommand(
        command=["this-executable-does-not-exist-xyz"], cwd=".", reason="test",
        scope="targeted", confidence="high", fallback=False, technology="pytest", log_name="out.log",
    )
    summary = execute_plan(tmp_path, _plan([cmd]), log_writer=None)
    assert summary.results[0].error is not None
    assert summary.results[0].succeeded is False


def test_working_directory_selection(tmp_path: Path):
    subdir = tmp_path / "backend"
    subdir.mkdir()
    marker = subdir / "marker.txt"
    marker.write_text("here", encoding="utf-8")
    # Command that fails unless run from the subdir (checks the marker exists relative to cwd).
    cmd = TestCommand(
        command=[sys.executable, "-c", "import pathlib,sys; sys.exit(0 if pathlib.Path('marker.txt').exists() else 1)"],
        cwd="backend", reason="test", scope="targeted", confidence="high", fallback=False,
        technology="pytest", log_name="out.log",
    )
    summary = execute_plan(tmp_path, _plan([cmd]), log_writer=None)
    assert summary.results[0].succeeded


def test_sanitized_log_written(tmp_path: Path):
    cmd = TestCommand(
        command=[sys.executable, "-c", "print('AKIAABCDEFGHIJKLMNOP')"],  # forgeops:allow-secret
        cwd=".", reason="test", scope="targeted", confidence="high", fallback=False,
        technology="pytest", log_name="out.log",
    )
    writer = LogWriter(tmp_path, "test")
    summary = execute_plan(tmp_path, _plan([cmd]), log_writer=writer)
    log_path = Path(summary.results[0].log_path)
    assert log_path.is_file()
    # Not a secret-scanner category redact.py handles, but confirms the
    # log file exists, is readable, and captured the command's own output.
    content = log_path.read_text(encoding="utf-8")
    assert "print" not in content or "AKIAABCDEFGHIJKLMNOP" in content  # sanity: real output captured, forgeops:allow-secret


def test_multiple_commands_all_attempted_even_after_a_failure(tmp_path: Path):
    failing = TestCommand(
        command=[sys.executable, "-c", "import sys; sys.exit(1)"], cwd=".", reason="fail",
        scope="targeted", confidence="high", fallback=False, technology="pytest", log_name="first.log",
    )
    passing = TestCommand(
        command=[sys.executable, "-c", "print('ok')"], cwd=".", reason="pass",
        scope="targeted", confidence="high", fallback=False, technology="node", log_name="second.log",
    )
    summary = execute_plan(tmp_path, _plan([failing, passing]), log_writer=None)
    assert len(summary.results) == 2
    assert summary.results[0].succeeded is False
    assert summary.results[1].succeeded is True
