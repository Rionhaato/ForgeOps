"""Integration tests for `forgeops checkpoint` (forgeops/cli/checkpoint.py):
human/JSON output, dry-run, atomic write, secret sanitization, schema
handling, and error paths."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from forgeops.cli.checkpoint import render_human, run_checkpoint
from forgeops.core import exit_codes


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def test_writes_current_state_json(git_repo):
    result = run_checkpoint(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    state_path = git_repo / ".agent" / "CURRENT_STATE.json"
    assert state_path.is_file()
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["branch"] is not None


def test_human_output_contains_summary_and_checks(git_repo):
    result = run_checkpoint(str(git_repo), write_log=False)
    rendered = render_human(result)
    assert "forgeops checkpoint" in rendered
    assert "Checkpoint write" in rendered


def test_json_output_is_valid_and_matches_result(git_repo):
    result = run_checkpoint(str(git_repo), write_log=False)
    payload = json.loads(result.to_json())
    assert payload["command"] == "checkpoint"
    assert payload["exit_code"] == result.exit_code
    assert payload["data"]["state_path"] == str(git_repo / ".agent" / "CURRENT_STATE.json")


def test_dry_run_does_not_write_file(git_repo):
    state_path = git_repo / ".agent" / "CURRENT_STATE.json"
    assert not state_path.exists()
    result = run_checkpoint(str(git_repo), write_log=False, dry_run=True)
    assert result.exit_code == exit_codes.SUCCESS
    assert not state_path.exists()
    assert result.data["dry_run"] is True


def test_dry_run_reports_what_would_be_written(git_repo):
    result = run_checkpoint(str(git_repo), write_log=False, dry_run=True)
    ids = {c.id: c for c in result.checks}
    assert ids["checkpoint-write"].status == "informational"
    assert "dry-run" in ids["checkpoint-write"].message


def test_atomic_replacement_preserves_narrative_across_runs(git_repo):
    state_path = git_repo / ".agent" / "CURRENT_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "schema_version": 1, "branch": "x", "mission": "important mission",
        "completed_work": ["a"], "blockers": [], "next_action": "b",
    }), encoding="utf-8")

    run_checkpoint(str(git_repo), write_log=False)
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["mission"] == "important mission"
    assert data["completed_work"] == ["a"]


def test_second_run_reflects_new_working_tree_state(git_repo):
    # First run: nothing untracked yet (the checkpoint file it writes
    # doesn't exist until after this run's own status snapshot was taken).
    first = run_checkpoint(str(git_repo), write_log=False)
    assert first.data["checkpoint"]["changed_files"]["untracked"] == 0

    (git_repo / "untracked.txt").write_text("x", encoding="utf-8")
    # Second run's status snapshot now sees both the new file and the
    # previous run's own (still-uncommitted) CURRENT_STATE.json.
    result = run_checkpoint(str(git_repo), write_log=False)
    assert result.data["checkpoint"]["changed_files"]["untracked"] == 2


def test_repo_not_found_returns_correct_exit_code(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    result = run_checkpoint(str(outside), write_log=False)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND


def test_git_unavailable_returns_command_execution_failure(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.checkpoint.git_version", lambda: None)
    result = run_checkpoint(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE


def test_unsupported_schema_version_is_a_warning_not_a_failure(git_repo):
    state_path = git_repo / ".agent" / "CURRENT_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"schema_version": 999, "mission": "future"}), encoding="utf-8")
    result = run_checkpoint(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    ids = {c.id: c for c in result.checks}
    assert ids["previous-state-schema"].status == "warning"
    # The file must still be written (recovered with fresh defaults), not left in a blocked state.
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1


def test_write_failure_maps_to_command_execution_failure(git_repo, monkeypatch):
    def boom(path, content, encoding="utf-8"):
        raise OSError("simulated disk full")

    monkeypatch.setattr("forgeops.cli.checkpoint.atomic_write_text", boom)
    result = run_checkpoint(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    ids = {c.id: c for c in result.checks}
    assert ids["checkpoint-write"].status == "fail"


def test_write_failure_leaves_no_partial_destination(git_repo, monkeypatch):
    state_path = git_repo / ".agent" / "CURRENT_STATE.json"

    def boom(path, content, encoding="utf-8"):
        raise OSError("simulated disk full")

    monkeypatch.setattr("forgeops.cli.checkpoint.atomic_write_text", boom)
    run_checkpoint(str(git_repo), write_log=False)
    assert not state_path.exists()


def test_never_leaks_secret_shaped_env_values_in_output(git_repo, monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "leaked-secret-value-12345")  # forgeops:allow-secret
    result = run_checkpoint(str(git_repo), write_log=False)
    assert "leaked-secret-value-12345" not in result.to_json()
    assert "leaked-secret-value-12345" not in render_human(result)


def test_repo_path_with_spaces_is_supported(spacey_git_repo):
    result = run_checkpoint(str(spacey_git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert (spacey_git_repo / ".agent" / "CURRENT_STATE.json").is_file()


def test_no_commits_yet_repository_still_produces_a_result(bare_git_repo):
    result = run_checkpoint(str(bare_git_repo), write_log=False)
    assert result.exit_code in (exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT)
    assert result.data["checkpoint"]["head"] is None
