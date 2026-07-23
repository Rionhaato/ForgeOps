"""Integration tests for `forgeops resume-context`: CLI wiring, exit
codes, JSON/human rendering, and the read-only guarantee. Pure-builder
coverage lives in tests/unit/test_resume_context.py."""
from __future__ import annotations

import json

from forgeops.cli.resume_context import render_human, run_resume_context
from forgeops.core import exit_codes


def test_clean_repo_with_established_checkpoint_returns_success(git_repo):
    from forgeops.cli.checkpoint import run_checkpoint
    run_checkpoint(str(git_repo))  # establishes a valid .agent/CURRENT_STATE.json
    result = run_resume_context(str(git_repo))
    assert result.exit_code == exit_codes.SUCCESS
    assert result.command == "resume-context"


def test_missing_state_file_returns_warnings_present(git_repo):
    result = run_resume_context(str(git_repo))
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    assert any(c.id == "state-missing" for c in result.checks)


def test_repo_not_found_returns_repo_not_found(tmp_path):
    result = run_resume_context(str(tmp_path / "does-not-exist"))
    assert result.exit_code == exit_codes.REPO_NOT_FOUND


def test_json_output_is_valid_and_matches_schema(git_repo):
    result = run_resume_context(str(git_repo))
    payload = json.loads(result.to_json())
    assert payload["command"] == "resume-context"
    assert "data" in payload
    assert "phase" in payload["data"]
    assert "stop_boundary" in payload["data"]
    assert "approval_boundaries" in payload["data"]


def test_human_render_contains_key_fields(git_repo):
    result = run_resume_context(str(git_repo))
    rendered = render_human(result)
    assert "repository:" in rendered
    assert "branch:" in rendered
    assert "stop boundary:" in rendered


def test_never_writes_any_file(git_repo):
    before = sorted(p.relative_to(git_repo) for p in git_repo.rglob("*") if p.is_file())
    run_resume_context(str(git_repo))
    after = sorted(p.relative_to(git_repo) for p in git_repo.rglob("*") if p.is_file())
    assert before == after


def test_output_size_check_reports_pass(git_repo):
    result = run_resume_context(str(git_repo))
    size_check = next(c for c in result.checks if c.id == "output-size")
    assert size_check.status == "pass"
