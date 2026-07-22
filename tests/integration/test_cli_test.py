from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from forgeops.cli.test import render_human, run_test_targeted
from forgeops.core import exit_codes


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _write_python_project(root: Path) -> None:
    (root / "pyproject.toml").write_text("[project]\nname='x'\n[tool.pytest.ini_options]\n", encoding="utf-8")


def test_plan_only_mode_runs_nothing(git_repo):
    _write_python_project(git_repo)
    (git_repo / "app.py").write_text("x = 1", encoding="utf-8")
    result = run_test_targeted(str(git_repo), write_log=False, plan_only=True)
    assert "execution" not in result.data
    assert result.data["plan"]["commands"]


def test_dry_run_mode_runs_nothing(git_repo):
    _write_python_project(git_repo)
    (git_repo / "app.py").write_text("x = 1", encoding="utf-8")
    result = run_test_targeted(str(git_repo), write_log=False, dry_run=True)
    assert "execution" not in result.data


def test_dry_run_does_not_modify_repo(git_repo):
    _write_python_project(git_repo)
    (git_repo / "app.py").write_text("x = 1", encoding="utf-8")
    before = subprocess.run(["git", "status", "--porcelain"], cwd=str(git_repo), capture_output=True, text=True).stdout
    run_test_targeted(str(git_repo), write_log=False, dry_run=True)
    after = subprocess.run(["git", "status", "--porcelain"], cwd=str(git_repo), capture_output=True, text=True).stdout
    assert before == after


def test_no_changes_plan_only_is_success(git_repo):
    result = run_test_targeted(str(git_repo), write_log=False, plan_only=True)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["plan"]["commands"] == []


def test_execution_runs_a_real_passing_pytest(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_test_targeted(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["execution"][0]["succeeded"] is True


def test_execution_reports_a_real_failing_pytest(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_fail.py").write_text("def test_fail():\n    assert False\n", encoding="utf-8")
    result = run_test_targeted(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    assert result.data["execution"][0]["succeeded"] is False
    assert result.data["execution"][0]["returncode"] != 0


def test_execution_never_hides_a_failure_in_checks(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_fail.py").write_text("def test_fail():\n    assert False\n", encoding="utf-8")
    result = run_test_targeted(str(git_repo), write_log=False)
    assert any(c.status == "fail" for c in result.checks)


def test_working_directory_selection_for_subdirectory_project(git_repo):
    backend = git_repo / "backend"
    backend.mkdir()
    (backend / "pyproject.toml").write_text("[project]\nname='x'\n[tool.pytest.ini_options]\n", encoding="utf-8")
    (backend / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_test_targeted(str(git_repo), write_log=False)
    assert result.data["execution"][0]["cwd"] == "backend"
    assert result.data["execution"][0]["succeeded"] is True


def test_sanitized_logs_written(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_test_targeted(str(git_repo), write_log=True)
    log_path = Path(result.data["execution"][0]["log_path"])
    assert log_path.is_file()


def test_json_output_includes_plan_and_execution(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_test_targeted(str(git_repo), write_log=False)
    parsed = json.loads(result.to_json())
    assert "plan" in parsed["data"]
    assert "execution" in parsed["data"]


def test_human_output_shows_plan_and_execution(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_test_targeted(str(git_repo), write_log=False)
    text = render_human(result)
    assert "plan:" in text
    assert "execution:" in text


def test_no_dependency_installation_ever_attempted(git_repo, monkeypatch):
    """Guard against a regression that would shell out to pip/npm install -
    subprocess execution is limited to exactly the commands the planner
    selected."""
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    calls: list[list[str]] = []
    import forgeops.core.subprocess_utils as subprocess_utils
    real_run = subprocess_utils.run

    def spy(args, cwd=None, timeout=subprocess_utils.DEFAULT_TIMEOUT_SECONDS):
        calls.append(list(args))
        return real_run(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(subprocess_utils, "run", spy)
    monkeypatch.setattr("forgeops.testing.executor.run", spy)
    run_test_targeted(str(git_repo), write_log=False)
    joined = [" ".join(c) for c in calls]
    assert not any("install" in c for c in joined)
    assert not any(c[0] in ("pip", "npm", "pnpm", "yarn") for c in calls if c)


def test_repo_not_found(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    result = run_test_targeted(str(outside), write_log=False, plan_only=True)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND


def test_unsupported_stack_returns_warning_exit_code(git_repo):
    (git_repo / "main.go").write_text("package main", encoding="utf-8")
    result = run_test_targeted(str(git_repo), write_log=False, plan_only=True)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
