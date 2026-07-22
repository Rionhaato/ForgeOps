from __future__ import annotations

import sys

from forgeops.core.subprocess_utils import run


def test_missing_executable_reports_error_not_exception():
    result = run(["this-executable-does-not-exist-anywhere-xyz"])
    assert result.error is not None
    assert result.returncode is None
    assert result.ok is False


def test_successful_command():
    result = run([sys.executable, "-c", "print('hello')"])
    assert result.ok is True
    assert result.stdout.strip() == "hello"


def test_nonzero_exit_is_not_an_error_field(tmp_path):
    result = run([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert result.error is None
    assert result.returncode == 3
    assert result.ok is False


def test_timeout_is_reported_gracefully():
    result = run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)
    assert result.timed_out is True
    assert result.returncode is None
    assert result.ok is False
