"""Integration tests for `forgeops handoff` (forgeops/cli/handoff.py):
generated from checkpoint data, human/JSON output, dry-run, atomic
write, secret sanitization, schema handling, and error paths."""
from __future__ import annotations

import json
import subprocess

from forgeops.cli.handoff import render_human, run_handoff
from forgeops.core import exit_codes


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def test_writes_handoff_md(git_repo):
    result = run_handoff(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    handoff_path = git_repo / ".agent" / "HANDOFF.md"
    assert handoff_path.is_file()
    content = handoff_path.read_text(encoding="utf-8")
    assert "# Handoff" in content


def test_generated_from_valid_existing_state(git_repo):
    state_path = git_repo / ".agent" / "CURRENT_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "schema_version": 1, "branch": "x", "mission": "ship the feature",
        "completed_work": ["did thing one"], "blockers": [
            {"description": "a known gap", "severity": "low", "affects": "nothing critical"}
        ], "next_action": "do the next bounded step",
    }), encoding="utf-8")
    result = run_handoff(str(git_repo), write_log=False)
    content = (git_repo / ".agent" / "HANDOFF.md").read_text(encoding="utf-8")
    assert "did thing one" in content
    assert "do the next bounded step" in content
    assert "a known gap" in content


def test_works_without_a_pre_existing_checkpoint(git_repo):
    assert not (git_repo / ".agent" / "CURRENT_STATE.json").exists()
    result = run_handoff(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert (git_repo / ".agent" / "HANDOFF.md").is_file()


def test_clean_repository_working_tree_state(git_repo):
    run_handoff(str(git_repo), write_log=False)
    content = (git_repo / ".agent" / "HANDOFF.md").read_text(encoding="utf-8")
    assert "0 staged, 0 modified, 0 untracked" in content


def test_dirty_repository_working_tree_state(git_repo):
    (git_repo / "untracked.txt").write_text("x", encoding="utf-8")
    run_handoff(str(git_repo), write_log=False)
    content = (git_repo / ".agent" / "HANDOFF.md").read_text(encoding="utf-8")
    assert "0 staged, 0 modified, 1 untracked" in content


def test_includes_standard_validation_commands(git_repo):
    run_handoff(str(git_repo), write_log=False)
    content = (git_repo / ".agent" / "HANDOFF.md").read_text(encoding="utf-8")
    assert "python -m pytest tests -q" in content
    assert "git status --short" in content


def test_includes_approval_boundaries(git_repo):
    run_handoff(str(git_repo), write_log=False)
    content = (git_repo / ".agent" / "HANDOFF.md").read_text(encoding="utf-8")
    assert "Explicit approval is required first for" in content
    assert "destructive actions" in content


def test_includes_read_only_reference_repo_warning_when_configured(git_repo):
    state_path = git_repo / ".agent" / "CURRENT_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "schema_version": 1, "branch": "x", "mission": "m", "completed_work": [],
        "blockers": [], "next_action": "n",
        "repository": {"reference_repo_readonly": "C:\\SomeReadOnlyRepo"},
    }), encoding="utf-8")
    run_handoff(str(git_repo), write_log=False)
    content = (git_repo / ".agent" / "HANDOFF.md").read_text(encoding="utf-8")
    assert "C:\\SomeReadOnlyRepo" in content
    assert "never modify it" in content


def test_omits_reference_repo_section_when_not_configured(git_repo):
    run_handoff(str(git_repo), write_log=False)
    content = (git_repo / ".agent" / "HANDOFF.md").read_text(encoding="utf-8")
    assert "## Read-only reference repository" not in content


def test_dry_run_does_not_write_file(git_repo):
    handoff_path = git_repo / ".agent" / "HANDOFF.md"
    result = run_handoff(str(git_repo), write_log=False, dry_run=True)
    assert result.exit_code == exit_codes.SUCCESS
    assert not handoff_path.exists()
    assert result.data["dry_run"] is True
    assert "# Handoff" in result.data["handoff_markdown"]


def test_human_output(git_repo):
    result = run_handoff(str(git_repo), write_log=False)
    rendered = render_human(result)
    assert "forgeops handoff" in rendered


def test_json_output(git_repo):
    result = run_handoff(str(git_repo), write_log=False)
    payload = json.loads(result.to_json())
    assert payload["command"] == "handoff"
    assert payload["data"]["handoff_path"] == str(git_repo / ".agent" / "HANDOFF.md")


def test_atomic_write_replaces_existing_handoff(git_repo):
    handoff_path = git_repo / ".agent" / "HANDOFF.md"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text("stale content that must be replaced", encoding="utf-8")
    run_handoff(str(git_repo), write_log=False)
    content = handoff_path.read_text(encoding="utf-8")
    assert "stale content" not in content


def test_repo_not_found_returns_correct_exit_code(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    result = run_handoff(str(outside), write_log=False)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND


def test_git_unavailable_returns_command_execution_failure(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.handoff.git_version", lambda: None)
    result = run_handoff(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE


def test_unsupported_schema_version_is_a_warning_not_a_failure(git_repo):
    state_path = git_repo / ".agent" / "CURRENT_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"schema_version": 999, "mission": "future"}), encoding="utf-8")
    result = run_handoff(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    ids = {c.id: c for c in result.checks}
    assert ids["previous-state-schema"].status == "warning"
    assert (git_repo / ".agent" / "HANDOFF.md").is_file()


def test_write_failure_maps_to_command_execution_failure(git_repo, monkeypatch):
    def boom(path, content, encoding="utf-8"):
        raise OSError("simulated disk full")

    monkeypatch.setattr("forgeops.cli.handoff.atomic_write_text", boom)
    result = run_handoff(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    ids = {c.id: c for c in result.checks}
    assert ids["handoff-write"].status == "fail"


def test_write_failure_leaves_no_partial_destination(git_repo, monkeypatch):
    handoff_path = git_repo / ".agent" / "HANDOFF.md"

    def boom(path, content, encoding="utf-8"):
        raise OSError("simulated disk full")

    monkeypatch.setattr("forgeops.cli.handoff.atomic_write_text", boom)
    run_handoff(str(git_repo), write_log=False)
    assert not handoff_path.exists()


def test_never_leaks_secret_shaped_env_values(git_repo, monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "leaked-secret-value-12345")  # forgeops:allow-secret
    result = run_handoff(str(git_repo), write_log=False)
    assert "leaked-secret-value-12345" not in result.to_json()
    assert "leaked-secret-value-12345" not in render_human(result)
    assert "leaked-secret-value-12345" not in result.data["handoff_markdown"]


def test_repo_path_with_spaces_is_supported(spacey_git_repo):
    result = run_handoff(str(spacey_git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert (spacey_git_repo / ".agent" / "HANDOFF.md").is_file()


def test_no_trailing_blank_line_at_eof(git_repo):
    """A blank line right before EOF trips `git diff --check` ('new blank
    line at EOF') - the file must end with exactly one trailing newline,
    not an extra blank line."""
    run_handoff(str(git_repo), write_log=False)
    content = (git_repo / ".agent" / "HANDOFF.md").read_text(encoding="utf-8")
    assert content.endswith("\n")
    assert not content.endswith("\n\n")
