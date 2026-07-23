"""True subprocess-level tests: the actual `python -m forgeops` and
`forgeops` console-script entry points, not just the underlying Python
functions."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest


def _run_module(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "forgeops", *args],
        cwd=str(cwd), capture_output=True, text=True,
    )


def test_python_dash_m_forgeops_doctor(git_repo):
    result = _run_module(["doctor", "--repo", str(git_repo)], cwd=git_repo)
    assert "forgeops doctor" in result.stdout
    assert result.returncode in (0, 1)  # success or warnings-present


def test_python_dash_m_forgeops_status_json(git_repo):
    result = _run_module(["status", "--json", "--repo", str(git_repo)], cwd=git_repo)
    payload = json.loads(result.stdout)
    assert payload["command"] == "status"


def test_python_dash_m_forgeops_from_nested_cwd(git_repo):
    nested = git_repo / "sub" / "dir"
    nested.mkdir(parents=True)
    result = _run_module(["status", "--json"], cwd=nested)
    payload = json.loads(result.stdout)
    assert payload["repo_root"] == str(git_repo)


def test_python_dash_m_forgeops_not_yet_implemented_command_exits_nonzero(git_repo):
    # `worktree` remains registered-but-unimplemented; `init` graduated to a
    # real command (see forgeops/cli/init.py) and is covered by
    # tests/integration/test_cli_init.py instead.
    result = _run_module(["worktree", "--repo", str(git_repo)], cwd=git_repo)
    assert result.returncode == 1
    assert "not yet implemented" in result.stderr


def test_python_dash_m_forgeops_init_real_execution(tmp_path):
    result = _run_module(["init", str(tmp_path)], cwd=tmp_path)
    assert result.returncode == 0
    assert "forgeops init" in result.stdout
    assert (tmp_path / "CLAUDE.md").is_file()
    assert (tmp_path / ".agent" / "CURRENT_STATE.json").is_file()


def test_python_dash_m_forgeops_invalid_repo_path_exit_code(tmp_path):
    result = _run_module(["status", "--repo", str(tmp_path / "nope")], cwd=tmp_path)
    assert result.returncode == 4  # REPO_NOT_FOUND


@pytest.mark.skipif(shutil.which("forgeops") is None, reason="forgeops console script not installed on PATH")
def test_console_script_entry_point(git_repo):
    result = subprocess.run(
        ["forgeops", "doctor", "--repo", str(git_repo)],
        cwd=str(git_repo), capture_output=True, text=True,
    )
    assert "forgeops doctor" in result.stdout
