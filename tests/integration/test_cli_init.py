"""Integration tests for `forgeops init` (forgeops/cli/init.py): PATH
resolution (omitted/explicit/spacey/nonexistent/file-not-directory),
dry-run semantics, human/JSON output, idempotency, ownership/conflict
detection, non-Git behavior, TrendForge protection, atomic-write
rollback, and INTERNAL_ERROR handling."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from forgeops.cli.init import render_human, run_init
from forgeops.core import exit_codes


# --- PATH resolution -----------------------------------------------------

def test_omitted_path_uses_cwd(tmp_path):
    result = run_init(None, cwd=tmp_path, write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.repo_root == str(tmp_path)
    assert (tmp_path / "CLAUDE.md").is_file()


def test_explicit_target_path(tmp_path):
    target = tmp_path / "my-project"
    target.mkdir()
    result = run_init(str(target), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert (target / "CLAUDE.md").is_file()


def test_windows_path_with_spaces(tmp_path):
    target = tmp_path / "a project with spaces"
    target.mkdir()
    result = run_init(str(target), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert (target / ".agent" / "CURRENT_STATE.json").is_file()


def test_nonexistent_path_is_rejected(tmp_path):
    missing = tmp_path / "does-not-exist"
    result = run_init(str(missing), write_log=False)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND
    assert not missing.exists()


def test_target_is_a_file_is_rejected(tmp_path):
    file_target = tmp_path / "a-file.txt"
    file_target.write_text("x", encoding="utf-8")
    result = run_init(str(file_target), write_log=False)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND
    ids = {c.id: c for c in result.checks}
    assert "not a directory" in ids["target-resolution"].message


def test_does_not_traverse_into_parent_or_child_repository(tmp_path):
    # A managed structure one level up must never be touched when a
    # nested directory is the explicit target.
    parent = tmp_path
    (parent / ".agent").mkdir()
    (parent / ".agent" / "CURRENT_STATE.json").write_text("sentinel-parent", encoding="utf-8")
    child = parent / "child"
    child.mkdir()

    result = run_init(str(child), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.repo_root == str(child)
    assert (child / ".agent" / "CURRENT_STATE.json").is_file()
    # The parent's own (unrelated, non-JSON) file must be untouched.
    assert (parent / ".agent" / "CURRENT_STATE.json").read_text(encoding="utf-8") == "sentinel-parent"


# --- non-Git / Git behavior ----------------------------------------------

def test_non_git_directory_initializes_successfully(tmp_path):
    result = run_init(str(tmp_path), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["is_git_repo"] is False
    ids = {c.id: c for c in result.checks}
    assert "git repository: no" in ids["git-state"].message


def test_git_repository_is_detected(git_repo):
    (git_repo / "existing.txt").write_text("x", encoding="utf-8")
    result = run_init(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["is_git_repo"] is True


# --- clean initialization / idempotency -----------------------------------

def test_clean_initialization_creates_expected_structure(tmp_path):
    result = run_init(str(tmp_path), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert (tmp_path / ".agent").is_dir()
    assert (tmp_path / ".agent" / "CURRENT_STATE.json").is_file()
    assert (tmp_path / ".agent" / "PROJECT_FACTS.md").is_file()
    assert (tmp_path / ".agent" / "DECISIONS.md").is_file()
    assert (tmp_path / ".agent" / "HANDOFF.md").is_file()
    assert (tmp_path / "CLAUDE.md").is_file()
    data = json.loads((tmp_path / ".agent" / "CURRENT_STATE.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1


def test_idempotent_second_run_is_a_no_op_success(tmp_path):
    first = run_init(str(tmp_path), write_log=False)
    assert first.exit_code == exit_codes.SUCCESS
    before = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    second = run_init(str(tmp_path), write_log=False)
    assert second.exit_code == exit_codes.SUCCESS
    assert all(o["action"] == "preserved" for o in second.data["outcomes"])
    after = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert before == after


def test_existing_compatible_forgeops_initialization_completes_partial_state(tmp_path):
    # Simulate a prior partial init: only CURRENT_STATE.json exists.
    first = run_init(str(tmp_path), write_log=False)
    assert first.exit_code == exit_codes.SUCCESS
    (tmp_path / ".agent" / "HANDOFF.md").unlink()
    (tmp_path / ".agent" / "PROJECT_FACTS.md").unlink()

    second = run_init(str(tmp_path), write_log=False)
    assert second.exit_code == exit_codes.SUCCESS
    assert (tmp_path / ".agent" / "HANDOFF.md").is_file()
    assert (tmp_path / ".agent" / "PROJECT_FACTS.md").is_file()
    outcomes = {o["key"]: o["action"] for o in second.data["outcomes"]}
    assert outcomes["handoff"] == "created_file"
    assert outcomes["project_facts"] == "created_file"
    assert outcomes["current_state"] == "preserved"


# --- ownership / conflict detection ---------------------------------------

def test_existing_user_claude_md_conflict_blocks_with_no_writes(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# a user's own rules\n", encoding="utf-8")
    result = run_init(str(tmp_path), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    ids = {c.id: c for c in result.checks}
    assert ids["preflight-claude_md"].status == "blocked"
    # No write should occur at all when any conflict is detected.
    assert not (tmp_path / ".agent").exists()
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "# a user's own rules\n"


def test_malformed_existing_state_conflict_blocks_with_no_writes(tmp_path):
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "CURRENT_STATE.json").write_text("{not valid json", encoding="utf-8")
    result = run_init(str(tmp_path), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    # Existing malformed file must be left completely untouched.
    assert (tmp_path / ".agent" / "CURRENT_STATE.json").read_text(encoding="utf-8") == "{not valid json"
    assert not (tmp_path / "CLAUDE.md").exists()


def test_no_writes_at_all_when_any_conflict_present(tmp_path):
    # Even paths that would otherwise be fine to create must not be
    # written when a different managed path conflicts.
    (tmp_path / "CLAUDE.md").write_text("# user file\n", encoding="utf-8")
    result = run_init(str(tmp_path), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert not (tmp_path / ".agent").exists()


# --- dry-run ---------------------------------------------------------------

def test_dry_run_performs_no_writes(tmp_path):
    result = run_init(str(tmp_path), write_log=False, dry_run=True)
    assert result.exit_code == exit_codes.SUCCESS
    assert not (tmp_path / ".agent").exists()
    assert not (tmp_path / "CLAUDE.md").exists()
    assert result.data["dry_run"] is True


def test_dry_run_reports_conflicts_and_blocking_exit_code(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# user file\n", encoding="utf-8")
    result = run_init(str(tmp_path), write_log=False, dry_run=True)
    # Dry-run must return the same blocking exit code a real run would.
    assert result.exit_code == exit_codes.BLOCKED
    assert not (tmp_path / ".agent").exists()


def test_dry_run_human_and_json_agree_semantically(tmp_path):
    result = run_init(str(tmp_path), write_log=False, dry_run=True)
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops init" in rendered
    assert payload["exit_code"] == result.exit_code
    assert payload["data"]["dry_run"] is True
    for p in payload["data"]["paths"]:
        assert p["state"] == "missing"


# --- human / JSON output ---------------------------------------------------

def test_human_output_contains_summary_and_checks(tmp_path):
    result = run_init(str(tmp_path), write_log=False)
    rendered = render_human(result)
    assert "forgeops init" in rendered
    assert "Initialization write" in rendered


def test_json_output_is_valid_and_matches_result(tmp_path):
    result = run_init(str(tmp_path), write_log=False)
    payload = json.loads(result.to_json())
    assert payload["command"] == "init"
    assert payload["exit_code"] == result.exit_code
    assert payload["data"]["target"] == str(tmp_path)


# --- atomic write failure / rollback ---------------------------------------

def test_write_failure_rolls_back_and_maps_to_command_execution_failure(tmp_path, monkeypatch):
    def boom(target, plan, clock=None):
        return [], "simulated disk full"

    monkeypatch.setattr("forgeops.cli.init.apply_init_plan", boom)
    result = run_init(str(tmp_path), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    ids = {c.id: c for c in result.checks}
    assert ids["write"].status == "fail"


def test_atomic_write_failure_leaves_no_partial_initialization(tmp_path, monkeypatch):
    call_count = {"n": 0}
    from forgeops.state.atomic_write import atomic_write_text as real_atomic_write_text

    def flaky(path, content, encoding="utf-8"):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise OSError("simulated disk full")
        real_atomic_write_text(path, content, encoding=encoding)

    monkeypatch.setattr("forgeops.state.project_init.atomic_write_text", flaky)
    result = run_init(str(tmp_path), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    assert not (tmp_path / ".agent").exists()
    assert not (tmp_path / "CLAUDE.md").exists()


# --- TrendForge protection ---------------------------------------------------

def test_trendforge_target_is_rejected(tmp_path, monkeypatch):
    fake_reference = tmp_path / "FakeTrendForge"
    fake_reference.mkdir()
    monkeypatch.setattr("forgeops.core.paths.READONLY_REFERENCE_REPO", fake_reference)
    result = run_init(str(fake_reference), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert not (fake_reference / ".agent").exists()
    assert not (fake_reference / "CLAUDE.md").exists()


def test_trendforge_subdirectory_target_is_rejected(tmp_path, monkeypatch):
    fake_reference = tmp_path / "FakeTrendForge"
    nested = fake_reference / "backend"
    nested.mkdir(parents=True)
    monkeypatch.setattr("forgeops.core.paths.READONLY_REFERENCE_REPO", fake_reference)
    result = run_init(str(nested), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert not (nested / ".agent").exists()


# --- secrets / credentials never emitted ------------------------------------

def test_never_leaks_secret_shaped_env_values_in_output(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "leaked-secret-value-12345")  # forgeops:allow-secret
    result = run_init(str(tmp_path), write_log=False)
    assert "leaked-secret-value-12345" not in result.to_json()
    assert "leaked-secret-value-12345" not in render_human(result)


# --- other CLI commands unaffected ------------------------------------------

def test_existing_checkpoint_command_still_works(git_repo):
    from forgeops.cli.checkpoint import run_checkpoint
    result = run_checkpoint(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
