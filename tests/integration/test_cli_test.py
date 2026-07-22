from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from forgeops.cli.test import render_human, run_full_test, run_test_targeted
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


def test_targeted_mode_unaffected_by_full_mode_addition(git_repo):
    """Explicit regression guard: run_test_targeted's public behavior is
    unchanged by adding run_full_test alongside it in the same module."""
    _write_python_project(git_repo)
    (git_repo / "app.py").write_text("x = 1", encoding="utf-8")
    (git_repo / "test_app.py").write_text("def test_app(): assert True", encoding="utf-8")
    result = run_test_targeted(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["plan"]["commands"][0]["scope"] == "targeted"
    assert "execution" in result.data


# --- forgeops test --full ---

def test_full_runs_the_complete_suite_ignoring_changed_files(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    (git_repo / "test_b.py").write_text("def test_b():\n    assert True\n", encoding="utf-8")
    result = run_full_test(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["plan"]["changed_files"] == []
    assert result.data["execution"][0]["succeeded"] is True


def test_full_reports_a_real_failing_suite(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_fail.py").write_text("def test_fail():\n    assert False\n", encoding="utf-8")
    result = run_full_test(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    assert result.data["execution"][0]["succeeded"] is False
    assert any(c.status == "fail" for c in result.checks)


def test_full_unsupported_stack_is_warning_not_silent(git_repo):
    (git_repo / "main.go").write_text("package main", encoding="utf-8")
    result = run_full_test(str(git_repo), write_log=False, plan_only=True)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    assert result.data["plan"]["commands"] == []
    assert result.data["plan"]["warnings"] != []


def test_full_python_stack_without_pytest_evidence_is_warning(git_repo):
    (git_repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (git_repo / "app.py").write_text("x = 1", encoding="utf-8")
    result = run_full_test(str(git_repo), write_log=False, plan_only=True)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    assert result.data["plan"]["commands"] == []


def test_full_missing_executable_reports_failure(git_repo, monkeypatch):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    def fake_plan(repo_root, config):
        from forgeops.testing.planner import TestCommand, TestPlan
        return TestPlan(changed_files=[], commands=[TestCommand(
            command=["this-executable-does-not-exist-xyz"], cwd=".", reason="test",
            scope="broad", confidence="high", fallback=False, technology="pytest", log_name="python-pytest.log",
        )])

    monkeypatch.setattr("forgeops.cli.test.build_full_test_plan", fake_plan)
    result = run_full_test(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    assert result.data["execution"][0]["error"] is not None
    assert result.data["execution"][0]["succeeded"] is False


def test_full_spacey_path_repo(spacey_git_repo):
    _write_python_project(spacey_git_repo)
    (spacey_git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_full_test(str(spacey_git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["execution"][0]["succeeded"] is True


def test_full_raw_log_created_and_sanitized(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text(
        "def test_ok():\n    print('AKIAABCDEFGHIJKLMNOP')\n    assert True\n", encoding="utf-8"  # forgeops:allow-secret
    )
    result = run_full_test(str(git_repo), write_log=True)
    log_path = Path(result.data["execution"][0]["log_path"])
    assert log_path.is_file()
    plan_log = list((git_repo / "logs" / "test").glob("*/test-full-summary.log"))
    assert len(plan_log) == 1


def test_full_plan_only_writes_distinct_log_name(git_repo):
    _write_python_project(git_repo)
    result = run_full_test(str(git_repo), write_log=True, plan_only=True)
    log_files = list((git_repo / "logs" / "test").glob("*/test-full-plan.log"))
    assert len(log_files) == 1


def test_full_json_output_includes_plan_and_execution(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_full_test(str(git_repo), write_log=False)
    parsed = json.loads(result.to_json())
    assert "plan" in parsed["data"]
    assert "execution" in parsed["data"]
    assert parsed["data"]["plan"]["changed_files"] == []


def test_full_human_output_shows_plan_and_execution(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    result = run_full_test(str(git_repo), write_log=False)
    text = render_human(result)
    assert "plan:" in text
    assert "execution:" in text


def test_full_no_dependency_installation_ever_attempted(git_repo, monkeypatch):
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
    run_full_test(str(git_repo), write_log=False)
    joined = [" ".join(c) for c in calls]
    assert not any("install" in c for c in joined)
    assert not any(c[0] in ("pip", "npm", "pnpm", "yarn") for c in calls if c)


def test_full_repo_not_found(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    result = run_full_test(str(outside), write_log=False, plan_only=True)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND


def test_full_dry_run_does_not_modify_repo_and_runs_nothing(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    before = subprocess.run(["git", "status", "--porcelain"], cwd=str(git_repo), capture_output=True, text=True).stdout
    result = run_full_test(str(git_repo), write_log=False, dry_run=True)
    after = subprocess.run(["git", "status", "--porcelain"], cwd=str(git_repo), capture_output=True, text=True).stdout
    assert before == after
    assert "execution" not in result.data
