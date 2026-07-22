from __future__ import annotations

import json

from forgeops.cli.doctor import render_human, run_doctor
from forgeops.core import exit_codes


def test_doctor_against_clean_repo_reports_repo_found(git_repo):
    result = run_doctor(str(git_repo), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["repo-discovery"].status == "pass"
    assert ids["git-available"].status == "pass"
    assert result.exit_code in (exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT)


def test_doctor_without_a_repo_warns_not_fails(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    result = run_doctor(str(outside), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["repo-discovery"].status == "warning"
    # A missing repo alone must not produce a hard failure exit code.
    assert result.exit_code != exit_codes.COMMAND_EXECUTION_FAILURE


def test_doctor_json_round_trips(git_repo):
    result = run_doctor(str(git_repo), write_log=False)
    parsed = json.loads(result.to_json())
    assert parsed["command"] == "doctor"
    assert isinstance(parsed["checks"], list)
    assert all("id" in c and "status" in c for c in parsed["checks"])


def test_doctor_human_output_lists_every_check(git_repo):
    result = run_doctor(str(git_repo), write_log=False)
    text = render_human(result)
    for check in result.checks:
        assert check.label in text


def test_doctor_missing_claude_and_codex_are_warnings_not_failures(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.doctor.shutil.which", lambda name: None)
    result = run_doctor(str(git_repo), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["claude-executable"].status == "warning"
    assert ids["codex-executable"].status == "warning"
    assert result.exit_code != exit_codes.COMMAND_EXECUTION_FAILURE


def test_doctor_finds_claude_and_codex_when_present(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.doctor.shutil.which", lambda name: f"/fake/bin/{name}")
    result = run_doctor(str(git_repo), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["claude-executable"].status == "pass"
    assert ids["codex-executable"].status == "pass"


def test_doctor_missing_git_executable_fails(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.doctor.git_version", lambda: None)
    result = run_doctor(str(git_repo), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["git-available"].status == "fail"
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE


def test_doctor_invalid_config_reports_invalid_config_exit_code(git_repo):
    (git_repo / "pyproject.toml").write_text("not [ valid toml", encoding="utf-8")
    result = run_doctor(str(git_repo), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["config-validity"].status == "fail"
    assert result.exit_code == exit_codes.INVALID_CONFIG


def test_doctor_every_check_has_a_stable_id(git_repo):
    result = run_doctor(str(git_repo), write_log=False)
    ids = [c.id for c in result.checks]
    assert len(ids) == len(set(ids)), "check ids must be unique"
    assert all(c.replace("-", "").isalnum() for c in ids)


def test_doctor_writes_log_when_repo_found(git_repo):
    run_doctor(str(git_repo), write_log=True)
    log_files = list((git_repo / "logs" / "doctor").glob("*/doctor.log"))
    assert len(log_files) == 1


def test_doctor_from_nested_directory(git_repo):
    nested = git_repo / "a" / "b"
    nested.mkdir(parents=True)
    result = run_doctor(str(nested), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["repo-discovery"].status == "pass"
    assert result.repo_root == str(git_repo)


def test_doctor_spacey_path(spacey_git_repo):
    result = run_doctor(str(spacey_git_repo), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["repo-discovery"].status == "pass"
