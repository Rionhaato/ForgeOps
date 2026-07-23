"""Integration tests for `forgeops worktree list` (forgeops/cli/worktree.py:
run_worktree_list, read-only) and `forgeops worktree create`
(run_worktree_create, mutating with --dry-run support). See
docs/worktrees.md for the managed root, branch-naming rule, registry
schema, and non-goals this checkpoint deliberately does not implement
(remove/prune/merge/agent-assignment)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from forgeops.cli.worktree import render_human, run_worktree_create, run_worktree_list
from forgeops.core import exit_codes
from forgeops.state.worktree_create import WorktreeCreateOutcome
from forgeops.state.worktree_registry import load_registry
from forgeops.worktrees.git_worktree import WorktreeListResult
from forgeops.worktrees.naming import managed_root_for, worktree_path_for


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


# --- worktree list -----------------------------------------------------------


def test_list_primary_checkout_only(git_repo):
    result = run_worktree_list(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert len(result.data["worktrees"]) == 1
    assert result.data["worktrees"][0]["is_primary"] is True


def test_list_with_one_additional_worktree(git_repo, tmp_path):
    linked = tmp_path / "linked"
    _git(["worktree", "add", "-b", "feature", str(linked)], cwd=git_repo)
    result = run_worktree_list(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert len(result.data["worktrees"]) == 2
    branches = {w["branch"] for w in result.data["worktrees"]}
    assert "feature" in branches


def test_list_multiple_worktrees(git_repo, tmp_path):
    for name in ("wt-a", "wt-b", "wt-c"):
        _git(["worktree", "add", "-b", name, str(tmp_path / name)], cwd=git_repo)
    result = run_worktree_list(str(git_repo), write_log=False)
    assert len(result.data["worktrees"]) == 4


def test_list_spacey_repo_path(spacey_git_repo):
    result = run_worktree_list(str(spacey_git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert len(result.data["worktrees"]) == 1


def test_list_spacey_worktree_path(git_repo, tmp_path):
    spacey_linked = tmp_path / "a linked worktree with spaces"
    _git(["worktree", "add", "-b", "feature", str(spacey_linked)], cwd=git_repo)
    result = run_worktree_list(str(git_repo), write_log=False)
    paths = [w["path"] for w in result.data["worktrees"]]
    assert any("a linked worktree with spaces" in p for p in paths)


def test_list_detached_worktree(git_repo, tmp_path):
    head = _git(["rev-parse", "HEAD"], cwd=git_repo).stdout.strip()
    detached = tmp_path / "detached"
    _git(["worktree", "add", "--detach", str(detached), head], cwd=git_repo)
    result = run_worktree_list(str(git_repo), write_log=False)
    matching = [w for w in result.data["worktrees"] if w["detached"]]
    assert len(matching) == 1
    assert matching[0]["branch"] is None


def test_list_locked_worktree_reports_locked_field(git_repo, tmp_path):
    linked = tmp_path / "linked"
    _git(["worktree", "add", "-b", "feature", str(linked)], cwd=git_repo)
    _git(["worktree", "lock", "--reason", "manual test lock", str(linked)], cwd=git_repo)
    result = run_worktree_list(str(git_repo), write_log=False)
    matching = [w for w in result.data["worktrees"] if w["path"].endswith("linked") or "linked" in w["path"]]
    assert any(w["locked"] and w["locked_reason"] == "manual test lock" for w in matching)


def test_list_absent_registry_still_works(git_repo):
    result = run_worktree_list(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["worktrees"][0]["forgeops_registered"] is False


def test_list_compatible_registry_marks_registered_worktree(git_repo):
    create_result = run_worktree_create("demo", str(git_repo), write_log=False)
    assert create_result.exit_code == exit_codes.SUCCESS
    result = run_worktree_list(str(git_repo), write_log=False)
    managed = [w for w in result.data["worktrees"] if w["forgeops_registered"]]
    assert len(managed) == 1
    assert managed[0]["forgeops_name"] == "demo"
    assert managed[0]["in_managed_root"] is True


def test_list_stale_registry_entry_detected_not_removed(git_repo, tmp_path):
    create_result = run_worktree_create("demo", str(git_repo), write_log=False)
    assert create_result.exit_code == exit_codes.SUCCESS
    worktree_path = worktree_path_for(git_repo, "demo")
    # Remove the worktree via git directly (never via forgeops - this
    # checkpoint implements no removal path), leaving the registry stale.
    _git(["worktree", "remove", "--force", str(worktree_path)], cwd=git_repo)

    result = run_worktree_list(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    assert len(result.data["stale_registry_entries"]) == 1
    assert result.data["stale_registry_entries"][0]["name"] == "demo"
    # Still present, untouched - stale detection never deletes anything.
    registry = load_registry(git_repo)
    assert len(registry.records) == 1


def test_list_malformed_registry_reports_warning_but_still_lists(git_repo):
    registry_path = git_repo / ".agent" / "runtime" / "WORKTREE_REGISTRY.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{ not valid json", encoding="utf-8")
    result = run_worktree_list(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    assert len(result.data["worktrees"]) == 1


def test_list_malformed_git_output_degrades_to_warning(git_repo, monkeypatch):
    monkeypatch.setattr(
        "forgeops.cli.worktree.list_worktrees",
        lambda repo_root: WorktreeListResult(ok=True, entries=[], warnings=["simulated malformed porcelain output"]),
    )
    result = run_worktree_list(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    ids = {c.id: c for c in result.checks}
    assert ids["worktree-list-parse"].status == "warning"


def test_list_non_git_directory_returns_repo_not_found(tmp_path):
    result = run_worktree_list(str(tmp_path), write_log=False)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND


def test_list_read_only_no_mutation(git_repo):
    before = set(git_repo.rglob("*"))
    run_worktree_list(str(git_repo), write_log=False)
    after = set(git_repo.rglob("*"))
    assert before == after


def test_list_human_output(git_repo):
    result = run_worktree_list(str(git_repo), write_log=False)
    rendered = render_human(result)
    assert "forgeops worktree list" in rendered
    assert "primary" in rendered


def test_list_json_output(git_repo):
    result = run_worktree_list(str(git_repo), write_log=False)
    payload = json.loads(result.to_json())
    assert payload["command"] == "worktree-list"
    assert payload["exit_code"] == result.exit_code


def test_list_no_git_executable_returns_command_execution_failure(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.worktree.git_version", lambda: None)
    result = run_worktree_list(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE


def test_list_internal_error_path_not_swallowed_by_run_function(git_repo, monkeypatch):
    # run_worktree_list itself must not catch an unexpected internal
    # exception - that boundary lives in forgeops.cli.main() only.
    def boom(repo_root):
        raise RuntimeError("simulated internal bug")

    monkeypatch.setattr("forgeops.cli.worktree.list_worktrees", boom)
    with pytest.raises(RuntimeError):
        run_worktree_list(str(git_repo), write_log=False)


# --- worktree create: dry-run -------------------------------------------------


def test_create_dry_run_performs_zero_mutation(git_repo):
    before = set(git_repo.parent.rglob("*"))
    result = run_worktree_create("demo", str(git_repo), write_log=False, dry_run=True)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["dry_run"] is True
    assert result.data["would_proceed"] is True
    after = set(git_repo.parent.rglob("*"))
    assert before == after


def test_create_dry_run_reports_expected_fields(git_repo):
    result = run_worktree_create("demo", str(git_repo), write_log=False, dry_run=True)
    data = result.data
    assert data["worktree_path"] == str(worktree_path_for(git_repo, "demo"))
    assert data["branch"] == "forgeops/demo"
    assert data["base_ref"] == "HEAD"
    assert data["base_commit"]


def test_create_dry_run_human_and_json_agree_semantically(git_repo):
    result = run_worktree_create("demo", str(git_repo), write_log=False, dry_run=True)
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops worktree create" in rendered
    assert payload["data"]["dry_run"] is True
    assert payload["exit_code"] == result.exit_code
    assert str(payload["data"]["would_proceed"]) in rendered


def test_create_dry_run_reports_same_blocking_exit_code_as_real_run(git_repo):
    _git(["branch", "forgeops/demo"], cwd=git_repo)
    dry = run_worktree_create("demo", str(git_repo), write_log=False, dry_run=True)
    real = run_worktree_create("demo", str(git_repo), write_log=False, dry_run=False)
    assert dry.exit_code == exit_codes.BLOCKED
    assert dry.exit_code == real.exit_code


# --- worktree create: successful real creation --------------------------------


def test_create_real_creates_worktree_and_registry(git_repo):
    result = run_worktree_create("demo", str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    worktree_path = worktree_path_for(git_repo, "demo")
    assert worktree_path.is_dir()
    registry = load_registry(git_repo)
    assert len(registry.records) == 1
    assert registry.records[0].path == str(worktree_path)


def test_create_default_branch_naming(git_repo):
    result = run_worktree_create("my-feature", str(git_repo), write_log=False)
    assert result.data["branch"] == "forgeops/my-feature"
    assert result.data["branch_source"] == "derived"


def test_create_explicit_branch(git_repo):
    result = run_worktree_create("demo", str(git_repo), write_log=False, branch="custom/branch-name")
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["branch"] == "custom/branch-name"
    assert result.data["branch_source"] == "explicit"


def test_create_explicit_base(git_repo):
    _git(["tag", "v1"], cwd=git_repo)
    head = _git(["rev-parse", "HEAD"], cwd=git_repo).stdout.strip()
    result = run_worktree_create("demo", str(git_repo), write_log=False, base="v1")
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["base_commit"] == head


def test_create_source_checkout_tracked_files_untouched(git_repo):
    before = (git_repo / "README.md").read_text(encoding="utf-8")
    run_worktree_create("demo", str(git_repo), write_log=False)
    assert (git_repo / "README.md").read_text(encoding="utf-8") == before


def test_create_no_writes_outside_managed_root(git_repo):
    result = run_worktree_create("demo", str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    worktree_path = Path(result.data["worktree_path"])
    assert worktree_path.is_relative_to(managed_root_for(git_repo))


def test_create_registry_written_atomically_valid_json(git_repo):
    run_worktree_create("demo", str(git_repo), write_log=False)
    registry_path = git_repo / ".agent" / "runtime" / "WORKTREE_REGISTRY.json"
    json.loads(registry_path.read_text(encoding="utf-8"))  # must not raise


def test_create_spacey_repo_and_name(spacey_git_repo):
    result = run_worktree_create("demo", str(spacey_git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert Path(result.data["worktree_path"]).is_dir()


def test_create_human_json_agree_on_success(git_repo):
    result = run_worktree_create("demo", str(git_repo), write_log=False)
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops worktree create" in rendered
    assert payload["exit_code"] == exit_codes.SUCCESS


def test_create_no_dependency_or_remote_side_effects(git_repo):
    run_worktree_create("demo", str(git_repo), write_log=False)
    remotes = _git(["remote", "-v"], cwd=git_repo).stdout.strip()
    assert remotes == ""


# --- worktree create: conflicts -----------------------------------------------


def test_create_missing_base_ref_blocks(git_repo):
    result = run_worktree_create("demo", str(git_repo), write_log=False, base="does-not-exist-ref")
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "base-ref-not-found" for c in result.data["conflicts"])


def test_create_existing_destination_blocks(git_repo):
    dest = worktree_path_for(git_repo, "demo")
    dest.mkdir(parents=True)
    result = run_worktree_create("demo", str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "destination-exists" for c in result.data["conflicts"])


def test_create_destination_traversal_attempt_is_rejected(git_repo):
    result = run_worktree_create("../escape", str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-name" for c in result.data["conflicts"])


def test_create_absolute_name_rejected(git_repo):
    result = run_worktree_create(str(git_repo.parent / "evil"), str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-name" for c in result.data["conflicts"])


def test_create_invalid_ambiguous_name_rejected(git_repo):
    result = run_worktree_create("has space", str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-name" for c in result.data["conflicts"])


def test_create_existing_branch_conflict(git_repo):
    _git(["branch", "forgeops/demo"], cwd=git_repo)
    result = run_worktree_create("demo", str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "branch-already-exists" for c in result.data["conflicts"])


def test_create_branch_already_checked_out_elsewhere(git_repo, tmp_path):
    other = tmp_path / "other-checkout"
    _git(["worktree", "add", "-b", "shared-branch", str(other)], cwd=git_repo)
    result = run_worktree_create("demo", str(git_repo), write_log=False, branch="shared-branch")
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "branch-checked-out-elsewhere" for c in result.data["conflicts"])


def test_create_duplicate_worktree_name_conflict(git_repo):
    first = run_worktree_create("demo", str(git_repo), write_log=False)
    assert first.exit_code == exit_codes.SUCCESS
    second = run_worktree_create("demo", str(git_repo), write_log=False)
    assert second.exit_code == exit_codes.BLOCKED


def test_create_malformed_registry_blocks_creation(git_repo):
    registry_path = git_repo / ".agent" / "runtime" / "WORKTREE_REGISTRY.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{ not valid json", encoding="utf-8")
    result = run_worktree_create("demo", str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "registry-malformed" for c in result.data["conflicts"])
    # Refusing to create must not have touched the malformed file.
    assert registry_path.read_text(encoding="utf-8") == "{ not valid json"


def test_create_non_git_directory_returns_repo_not_found(tmp_path):
    result = run_worktree_create("demo", str(tmp_path), write_log=False)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND


def test_create_bare_repository_target_is_never_a_usable_target(tmp_path):
    # A true bare repository has no `.git` entry anywhere beneath it (the
    # bare directory itself plays that role), so the shared repo-root
    # discovery every command reuses (forgeops.core.paths.resolve_repo_root)
    # cannot discover it at all - this is a known, documented limitation
    # (see docs/worktrees.md), not a gap specific to worktree create. The
    # command still safely refuses to act on it, via REPO_NOT_FOUND rather
    # than the BLOCKED "bare-repository" conflict path (that path is
    # exercised directly at the build_worktree_create_plan level in
    # tests/unit/test_worktree_create.py, which does not go through
    # repo-root discovery).
    bare = tmp_path / "bare.git"
    _git(["init", "-q", "--bare", str(bare)], cwd=tmp_path)
    result = run_worktree_create("demo", str(bare), write_log=False)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND
    assert not (bare / "demo").exists()


def test_create_no_writes_when_blocked(git_repo):
    _git(["branch", "forgeops/demo"], cwd=git_repo)
    before = set(git_repo.parent.rglob("*"))
    run_worktree_create("demo", str(git_repo), write_log=False)
    after = set(git_repo.parent.rglob("*"))
    assert before == after


# --- worktree create: TrendForge / protected target ---------------------------


def test_create_trendforge_target_is_rejected(tmp_path, monkeypatch):
    fake_reference = tmp_path / "FakeTrendForge"
    fake_reference.mkdir()
    _git(["init", "-q"], cwd=fake_reference)
    monkeypatch.setattr("forgeops.core.paths.READONLY_REFERENCE_REPO", fake_reference)
    result = run_worktree_create("demo", str(fake_reference), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert not (fake_reference.parent / ".forgeops-worktrees").exists()


# --- worktree create: simulated git failure / partial-failure reporting -------


def test_create_simulated_git_failure_reports_partial_state(git_repo, monkeypatch):
    monkeypatch.setattr(
        "forgeops.cli.worktree.apply_worktree_create",
        lambda repo_root, plan, clock=None: WorktreeCreateOutcome(
            ok=False, stdout="", stderr="fatal: simulated failure",
            partial_state={"directory_created": False, "branch_created": False, "worktree_registered_in_git": False},
            registry_written=False, registry_error=None,
        ),
    )
    result = run_worktree_create("demo", str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    assert result.data["partial_state"] is not None
    assert "manual_recovery_recommendation" in result.data
    assert "simulated failure" in result.data["git_stderr"]


def test_create_credentials_never_leaked_in_output(git_repo, monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "leaked-secret-value-12345")  # forgeops:allow-secret
    result = run_worktree_create("demo", str(git_repo), write_log=False)
    assert "leaked-secret-value-12345" not in result.to_json()
    assert "leaked-secret-value-12345" not in render_human(result)


def test_create_no_git_executable_returns_command_execution_failure(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.worktree.git_version", lambda: None)
    result = run_worktree_create("demo", str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE


# --- other CLI commands unaffected --------------------------------------------


def test_existing_checkpoint_command_still_works(git_repo):
    from forgeops.cli.checkpoint import run_checkpoint
    result = run_checkpoint(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


def test_existing_status_command_still_works(git_repo):
    from forgeops.cli.status import run_status
    result = run_status(str(git_repo))
    assert result.exit_code == exit_codes.SUCCESS
