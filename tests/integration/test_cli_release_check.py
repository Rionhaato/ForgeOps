from __future__ import annotations

import json
import subprocess
from pathlib import Path

from forgeops.cli.release_check import render_human, run_release_check
from forgeops.core import exit_codes


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _write_python_project(root: Path) -> None:
    (root / "pyproject.toml").write_text("[project]\nname='x'\n[tool.pytest.ini_options]\n", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "add project files"], root)


def test_fully_ready_repository(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-q", "-m", "add test"], git_repo)
    result = run_release_check(str(git_repo), write_log=False)
    assert result.data["release_ready"] is True
    assert result.data["blocking_checks"] == []
    assert result.exit_code in (exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT)


def test_dirty_working_tree_is_warning_not_blocker(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-q", "-m", "add test"], git_repo)
    (git_repo / "untracked.txt").write_text("x", encoding="utf-8")
    result = run_release_check(str(git_repo), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["working-tree-clean"].status == "warning"
    assert "working-tree-clean" in result.data["warning_checks"]
    assert result.data["release_ready"] is True  # a warning alone must not block readiness


def test_failing_full_tests_blocks_readiness(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_fail.py").write_text("def test_fail():\n    assert False\n", encoding="utf-8")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-q", "-m", "add failing test"], git_repo)
    result = run_release_check(str(git_repo), write_log=False)
    assert result.data["release_ready"] is False
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    assert any(cid.startswith("test.") for cid in result.data["blocking_checks"])


def test_audit_secret_finding_is_a_hard_blocker(git_repo):
    _write_python_project(git_repo)
    (git_repo / "config.py").write_text("KEY = 'AKIAABCDEFGHIJKLMNOP'\n", encoding="utf-8")  # forgeops:allow-secret
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-q", "-m", "oops"], git_repo)
    result = run_release_check(str(git_repo), write_log=False)
    assert result.data["release_ready"] is False
    assert result.exit_code == exit_codes.BLOCKED
    assert any(cid.startswith("audit.secret-scan") for cid in result.data["blocking_checks"])


def test_never_prints_the_secret_value(git_repo):
    secret_value = "AKIAABCDEFGHIJKLMNOP"  # forgeops:allow-secret
    _write_python_project(git_repo)
    (git_repo / "config.py").write_text(f"KEY = '{secret_value}'\n", encoding="utf-8")
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-q", "-m", "oops"], git_repo)
    result = run_release_check(str(git_repo), write_log=False)
    assert secret_value not in result.to_json()
    assert secret_value not in render_human(result)


def test_doctor_blocker_propagates(git_repo, monkeypatch):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-q", "-m", "add test"], git_repo)

    def fake_doctor(*args, **kwargs):
        from forgeops.core.result import CommandResult, Check
        return CommandResult(
            command="doctor", schema_version=1, generated_at="t", repo_root=str(git_repo),
            exit_code=exit_codes.COMMAND_EXECUTION_FAILURE, summary="1 fail",
            checks=[Check("git-available", "git executable", "fail", "simulated")],
        )

    monkeypatch.setattr("forgeops.cli.release_check.run_doctor", fake_doctor)
    result = run_release_check(str(git_repo), write_log=False)
    assert result.data["release_ready"] is False
    assert any(cid.startswith("doctor.") for cid in result.data["blocking_checks"])


def test_generated_artifact_findings_are_informational_per_audit_policy(git_repo):
    _write_python_project(git_repo)
    nm = git_repo / "node_modules"
    nm.mkdir()
    (nm / "pkg.js").write_text("x", encoding="utf-8")
    result = run_release_check(str(git_repo), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["audit.generated-artifact-dirs"].status == "informational"


def test_unavailable_node_build_validation_is_not_treated_as_success(git_repo):
    _write_python_project(git_repo)
    package = {"dependencies": {"react": "^18.0.0"}}
    (git_repo / "package.json").write_text(json.dumps(package), encoding="utf-8")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-q", "-m", "add package.json"], git_repo)
    result = run_release_check(str(git_repo), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["compile-validation-node"].status == "informational"
    assert "unavailable" in ids["compile-validation-node"].message


def test_unsupported_repository(tmp_path):
    from tests.conftest import init_git_repo
    repo = init_git_repo(tmp_path / "repo")
    (repo / "main.go").write_text("package main", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "go file"], repo)
    result = run_release_check(str(repo), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["compile-validation-python"].status == "informational"
    assert "unavailable" in ids["compile-validation-python"].message


def test_bare_repository_fails_branch_head_gate(bare_git_repo):
    result = run_release_check(str(bare_git_repo), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["branch-head-availability"].status == "fail"
    assert result.data["release_ready"] is False


def test_raw_log_evidence_written(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-q", "-m", "add test"], git_repo)
    run_release_check(str(git_repo), write_log=True)
    log_files = list((git_repo / "logs" / "release-check").glob("*/release-check.log"))
    assert len(log_files) == 1
    compileall_logs = list((git_repo / "logs" / "release-check").glob("*/compileall.log"))
    assert len(compileall_logs) == 1


def test_human_output(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-q", "-m", "add test"], git_repo)
    result = run_release_check(str(git_repo), write_log=False)
    text = render_human(result)
    assert "forgeops release-check" in text
    assert "RELEASE READY" in text or "NOT RELEASE READY" in text


def test_json_output_has_stable_structure(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-q", "-m", "add test"], git_repo)
    result = run_release_check(str(git_repo), write_log=False)
    parsed = json.loads(result.to_json())
    assert set(["release_ready", "overall_exit_code", "blocking_checks", "warning_checks", "gate_results"]) <= set(parsed["data"].keys())
    assert set(parsed["data"]["gate_results"].keys()) == {"doctor", "audit", "test_full"}


def test_deterministic_overall_readiness_result(git_repo):
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-q", "-m", "add test"], git_repo)
    a = run_release_check(str(git_repo), write_log=False)
    b = run_release_check(str(git_repo), write_log=False)
    assert a.data["release_ready"] == b.data["release_ready"]
    assert a.exit_code == b.exit_code


def test_repo_not_found(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    result = run_release_check(str(outside), write_log=False)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND
    assert result.data["release_ready"] is False


def test_missing_git_executable(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.release_check.git_version", lambda: None)
    result = run_release_check(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    assert result.data["release_ready"] is False


def test_never_pushes_deploys_or_configures_remote(git_repo):
    """No network-adjacent git subcommand is ever invoked - release-check
    only reads local state and runs the local test suite."""
    _write_python_project(git_repo)
    (git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-q", "-m", "add test"], git_repo)
    run_release_check(str(git_repo), write_log=False)
    remotes = subprocess.run(["git", "remote"], cwd=str(git_repo), capture_output=True, text=True).stdout
    assert remotes.strip() == ""


def test_spacey_path_repo(spacey_git_repo):
    _write_python_project(spacey_git_repo)
    (spacey_git_repo / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    _git(["add", "-A"], spacey_git_repo)
    _git(["commit", "-q", "-m", "add test"], spacey_git_repo)
    result = run_release_check(str(spacey_git_repo), write_log=False)
    assert result.data["release_ready"] is True
