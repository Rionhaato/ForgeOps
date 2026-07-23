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
    # `init` and `worktree` both graduated to real commands (see
    # forgeops/cli/init.py, forgeops/cli/worktree.py) and are covered by
    # tests/integration/test_cli_init.py / test_cli_worktree.py instead.
    result = _run_module(["agents", "--repo", str(git_repo)], cwd=git_repo)
    assert result.returncode == 1
    assert "not yet implemented" in result.stderr


def test_python_dash_m_forgeops_init_real_execution(tmp_path):
    result = _run_module(["init", str(tmp_path)], cwd=tmp_path)
    assert result.returncode == 0
    assert "forgeops init" in result.stdout
    assert (tmp_path / "CLAUDE.md").is_file()
    assert (tmp_path / ".agent" / "CURRENT_STATE.json").is_file()


def test_python_dash_m_forgeops_worktree_requires_a_subcommand(git_repo):
    # `worktree` requires an explicit `list`, `create`, or `remove`
    # subcommand - argparse itself enforces this (required=True on the
    # nested subparsers) and reports its own usage error, exit code 2.
    result = _run_module(["worktree", "--repo", str(git_repo)], cwd=git_repo)
    assert result.returncode == 2


def test_python_dash_m_forgeops_worktree_list_real_execution(git_repo):
    result = _run_module(["worktree", "list", "--repo", str(git_repo)], cwd=git_repo)
    assert result.returncode == 0
    assert "forgeops worktree list" in result.stdout


def test_python_dash_m_forgeops_worktree_create_real_execution(git_repo):
    result = _run_module(["worktree", "create", "demo", "--repo", str(git_repo)], cwd=git_repo)
    assert result.returncode == 0
    assert "forgeops worktree create" in result.stdout
    assert (git_repo.parent / ".forgeops-worktrees" / git_repo.name / "demo").is_dir()


def test_python_dash_m_forgeops_worktree_remove_real_execution(git_repo):
    create_result = _run_module(["worktree", "create", "demo", "--repo", str(git_repo)], cwd=git_repo)
    assert create_result.returncode == 0
    worktree_path = git_repo.parent / ".forgeops-worktrees" / git_repo.name / "demo"
    assert worktree_path.is_dir()

    no_confirm = _run_module(["worktree", "remove", "demo", "--repo", str(git_repo)], cwd=git_repo)
    assert no_confirm.returncode == 2  # BLOCKED: confirmation required
    assert worktree_path.is_dir()

    result = _run_module(["worktree", "remove", "demo", "--repo", str(git_repo), "--confirm"], cwd=git_repo)
    assert result.returncode == 0
    assert "forgeops worktree remove" in result.stdout
    assert not worktree_path.exists()


def test_python_dash_m_forgeops_task_lifecycle_real_execution(git_repo):
    init_result = _run_module(["init", str(git_repo)], cwd=git_repo)
    assert init_result.returncode == 0

    create_result = _run_module(["task", "create", "My Task", "--repo", str(git_repo)], cwd=git_repo)
    assert create_result.returncode == 0
    assert "forgeops task create" in create_result.stdout
    task_dir = git_repo / ".agent" / "tasks" / "task-0001"
    assert (task_dir / "TASK.json").is_file()

    list_result = _run_module(["task", "list", "--repo", str(git_repo)], cwd=git_repo)
    assert list_result.returncode == 0
    assert "task-0001" in list_result.stdout

    show_result = _run_module(["task", "show", "task-0001", "--repo", str(git_repo)], cwd=git_repo)
    assert show_result.returncode in (0, 1)  # SUCCESS or WARNINGS_PRESENT (placeholder sections)
    assert "task-0001" in show_result.stdout


def test_python_dash_m_forgeops_task_ownership_real_execution(git_repo):
    init_result = _run_module(["init", str(git_repo)], cwd=git_repo)
    assert init_result.returncode == 0

    create_result = _run_module(["task", "create", "Owned Task", "--repo", str(git_repo)], cwd=git_repo)
    assert create_result.returncode == 0

    worktree_result = _run_module(["worktree", "create", "demo", "--repo", str(git_repo)], cwd=git_repo)
    assert worktree_result.returncode == 0

    assign_result = _run_module(["task", "assign", "task-0001", "demo", "--repo", str(git_repo)], cwd=git_repo)
    assert assign_result.returncode == 0
    assert "forgeops task assign" in assign_result.stdout
    task_json = json.loads((git_repo / ".agent" / "tasks" / "task-0001" / "TASK.json").read_text(encoding="utf-8"))
    assert task_json["worktree_id"] == "demo"

    no_confirm = _run_module(["task", "unassign", "task-0001", "--repo", str(git_repo)], cwd=git_repo)
    assert no_confirm.returncode == 2  # BLOCKED: confirmation required
    task_json = json.loads((git_repo / ".agent" / "tasks" / "task-0001" / "TASK.json").read_text(encoding="utf-8"))
    assert task_json["worktree_id"] == "demo"

    unassign_result = _run_module(["task", "unassign", "task-0001", "--repo", str(git_repo), "--confirm"], cwd=git_repo)
    assert unassign_result.returncode == 0
    task_json = json.loads((git_repo / ".agent" / "tasks" / "task-0001" / "TASK.json").read_text(encoding="utf-8"))
    assert task_json["worktree_id"] is None


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
