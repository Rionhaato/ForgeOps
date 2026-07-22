from __future__ import annotations

import json
import subprocess

from forgeops.cli.status import render_human, run_status
from forgeops.core import exit_codes


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def test_status_clean_repo(git_repo):
    result = run_status(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["clean"] is True
    assert result.data["staged"]["count"] == 0


def test_status_dirty_repo_with_untracked_and_staged(git_repo):
    (git_repo / "untracked.txt").write_text("x", encoding="utf-8")
    (git_repo / "staged.txt").write_text("x", encoding="utf-8")
    _git(["add", "staged.txt"], git_repo)
    result = run_status(str(git_repo), write_log=False)
    assert result.data["clean"] is False
    assert "untracked.txt" in result.data["untracked"]["paths"]
    assert "staged.txt" in result.data["staged"]["paths"]


def test_status_detached_head(git_repo):
    head = run_status(str(git_repo), write_log=False).data["head"]
    _git(["checkout", head], git_repo)
    result = run_status(str(git_repo), write_log=False)
    assert result.data["detached_head"] is True
    ids = {c.id: c for c in result.checks}
    assert ids["detached-head"].status == "warning"


def test_status_no_remote(git_repo):
    result = run_status(str(git_repo), write_log=False)
    assert result.data["remotes"] == []
    assert result.data["upstream"] is None
    assert result.data["ahead"] is None
    assert result.data["behind"] is None


def test_status_remote_credentials_redacted(git_repo):
    _git(["remote", "add", "origin", "https://user:hunter2@example.invalid/r.git"], git_repo)
    result = run_status(str(git_repo), write_log=False)
    assert "hunter2" not in json.dumps(result.data)
    assert "hunter2" not in render_human(result)


def test_status_repo_not_found(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    result = run_status(str(outside), write_log=False)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND
    assert result.repo_root is None


def test_status_invalid_repo_path(tmp_path):
    result = run_status(str(tmp_path / "does-not-exist-at-all"), write_log=False)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND


def test_status_json_is_stable_across_calls(git_repo):
    a = run_status(str(git_repo), write_log=False, clock=lambda: __import__("datetime").datetime(2026, 1, 1))
    b = run_status(str(git_repo), write_log=False, clock=lambda: __import__("datetime").datetime(2026, 1, 1))
    assert a.to_json() == b.to_json()


def test_status_agent_state_file_missing_is_informational(git_repo):
    result = run_status(str(git_repo), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["agent-state-file"].status == "informational"


def test_status_agent_state_file_valid(git_repo):
    agent_dir = git_repo / ".agent"
    agent_dir.mkdir()
    (agent_dir / "CURRENT_STATE.json").write_text(json.dumps({
        "schema_version": 1, "branch": "main", "mission": "x",
        "completed_work": [], "blockers": [], "next_action": "y",
    }), encoding="utf-8")
    result = run_status(str(git_repo), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["agent-state-file"].status == "pass"


def test_status_agent_state_file_invalid_json_is_warning(git_repo):
    agent_dir = git_repo / ".agent"
    agent_dir.mkdir()
    (agent_dir / "CURRENT_STATE.json").write_text("{not json", encoding="utf-8")
    result = run_status(str(git_repo), write_log=False)
    ids = {c.id: c for c in result.checks}
    assert ids["agent-state-file"].status == "warning"
    assert result.exit_code == exit_codes.WARNINGS_PRESENT


def test_status_ambiguous_stack_reported(git_repo):
    (git_repo / "app.py").write_text("print(1)", encoding="utf-8")
    result = run_status(str(git_repo), write_log=False)
    stack = {f["technology"]: f for f in result.data["detected_stack"]}
    assert stack["python"]["ambiguous"] is True


def test_status_human_output_contains_branch_and_head(git_repo):
    result = run_status(str(git_repo), write_log=False)
    text = render_human(result)
    assert result.data["branch"] in text
    assert result.data["head"] in text


def test_status_missing_git_executable(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.status.git_version", lambda: None)
    result = run_status(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
