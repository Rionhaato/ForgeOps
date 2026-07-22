from __future__ import annotations

import shutil
import sys

import pytest

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


@pytest.mark.skipif(sys.platform != "win32" or shutil.which("npm") is None, reason="requires npm on Windows")
def test_windows_cmd_shim_executable_is_resolved():
    """Regression test: npm (and npx/pnpm/yarn) are installed as .cmd
    shims on Windows, which subprocess.run([...], shell=False) cannot
    find by bare name without PATHEXT resolution. Real execution against
    a disposable mixed-repo example during Phase 2B validation caught
    this - `npm test` silently reported "executable not found" even
    though `npm --version` worked fine from an interactive shell."""
    result = run(["npm", "--version"])
    assert result.error is None
    assert result.returncode == 0
    assert result.stdout.strip() != ""
    # The reported args stay the original, readable command - only the
    # actual subprocess invocation uses the resolved path internally.
    assert result.args == ("npm", "--version")


def test_reported_args_are_unaffected_by_resolution():
    result = run([sys.executable, "-c", "print('hi')"])
    assert result.args == (sys.executable, "-c", "print('hi')")
