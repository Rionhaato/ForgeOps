"""Integration tests for `forgeops worktree remove` (forgeops/cli/worktree.py:
run_worktree_remove) - confirmation-gated, mutating, with `--dry-run` and
`--delete-branch` support. See docs/worktrees.md for the eligibility
model this exercises end-to-end through the CLI entry point."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from forgeops.cli.worktree import render_human, run_worktree_create, run_worktree_list, run_worktree_remove
from forgeops.core import exit_codes
from forgeops.state.worktree_registry import load_registry


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _create(git_repo: Path, name: str = "demo", **kwargs):
    result = run_worktree_create(name, str(git_repo), write_log=False, **kwargs)
    assert result.exit_code == exit_codes.SUCCESS
    return result


# --- confirmation / dry-run ---------------------------------------------------


def test_missing_confirm_performs_no_mutation(git_repo):
    created = _create(git_repo)
    worktree_path = Path(created.data["worktree_path"])
    before = set(git_repo.parent.rglob("*"))
    result = run_worktree_remove("demo", str(git_repo), write_log=False)
    after = set(git_repo.parent.rglob("*"))
    assert result.exit_code == exit_codes.BLOCKED
    assert result.data["action"] == "confirmation_required"
    assert before == after
    assert worktree_path.is_dir()
    registry = load_registry(git_repo)
    assert registry.records[0].status == "active"


def test_dry_run_performs_no_mutation(git_repo):
    _create(git_repo)
    before = set(git_repo.parent.rglob("*"))
    result = run_worktree_remove("demo", str(git_repo), write_log=False, dry_run=True)
    after = set(git_repo.parent.rglob("*"))
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["action"] == "would_remove_worktree"
    assert before == after


def test_dry_run_and_real_preflight_agree_semantically(git_repo):
    _create(git_repo)
    dry = run_worktree_remove("demo", str(git_repo), write_log=False, dry_run=True)
    no_confirm = run_worktree_remove("demo", str(git_repo), write_log=False)
    assert dry.data["conflicts"] == no_confirm.data["conflicts"]
    assert dry.data["would_proceed"] == no_confirm.data["would_proceed"]


def test_dry_run_reports_same_blocked_exit_code_as_real_run_when_blocked(git_repo):
    dry = run_worktree_remove("does-not-exist", str(git_repo), write_log=False, dry_run=True)
    confirmed = run_worktree_remove("does-not-exist", str(git_repo), write_log=False, confirm=True)
    assert dry.exit_code == exit_codes.BLOCKED
    assert dry.exit_code == confirmed.exit_code


def test_unknown_worktree_name_blocked(git_repo):
    result = run_worktree_remove("no-such-worktree", str(git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "not-registered" for c in result.data["conflicts"])


def test_absolute_name_rejected(git_repo):
    result = run_worktree_remove(str(git_repo.parent / "evil"), str(git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-name" for c in result.data["conflicts"])


def test_traversal_rejected(git_repo):
    result = run_worktree_remove("../escape", str(git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-name" for c in result.data["conflicts"])


def test_unregistered_git_worktree_rejected(git_repo, tmp_path):
    # A worktree that exists in Git but was never created by `forgeops
    # worktree create` has no registry record at all.
    linked = tmp_path / "unregistered"
    _git(["worktree", "add", "-b", "feature", str(linked)], cwd=git_repo)
    result = run_worktree_remove("unregistered", str(git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "not-registered" for c in result.data["conflicts"])


def test_stale_registry_only_entry_rejected(git_repo):
    created = _create(git_repo)
    worktree_path = Path(created.data["worktree_path"])
    _git(["worktree", "remove", "--force", str(worktree_path)], cwd=git_repo)
    result = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "stale-registry-entry" for c in result.data["conflicts"])


def test_malformed_registry_rejected(git_repo):
    _create(git_repo)
    registry_path = git_repo / ".agent" / "runtime" / "WORKTREE_REGISTRY.json"
    registry_path.write_text("{ not valid json", encoding="utf-8")
    result = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "registry-malformed" for c in result.data["conflicts"])


def test_locked_worktree_rejected(git_repo):
    created = _create(git_repo)
    worktree_path = Path(created.data["worktree_path"])
    _git(["worktree", "lock", "--reason", "manual test lock", str(worktree_path)], cwd=git_repo)
    result = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "worktree-locked" for c in result.data["conflicts"])


def test_path_outside_managed_root_rejected_via_tampered_registry(git_repo):
    _create(git_repo)
    registry_path = git_repo / ".agent" / "runtime" / "WORKTREE_REGISTRY.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["records"][0]["path"] = str(git_repo.parent / "outside")
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    result = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED


def test_human_output(git_repo):
    _create(git_repo)
    result = run_worktree_remove("demo", str(git_repo), write_log=False, dry_run=True)
    rendered = render_human(result)
    assert "forgeops worktree remove" in rendered


def test_json_output(git_repo):
    _create(git_repo)
    result = run_worktree_remove("demo", str(git_repo), write_log=False, dry_run=True)
    payload = json.loads(result.to_json())
    assert payload["command"] == "worktree-remove"
    assert payload["exit_code"] == result.exit_code


# --- dirty / active refusal ---------------------------------------------------


def test_tracked_modification_refused(git_repo):
    created = _create(git_repo)
    worktree_path = Path(created.data["worktree_path"])
    (worktree_path / "README.md").write_text("changed", encoding="utf-8")
    result = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "dirty-worktree" for c in result.data["conflicts"])
    assert worktree_path.is_dir()


def test_untracked_file_refused(git_repo):
    created = _create(git_repo)
    worktree_path = Path(created.data["worktree_path"])
    (worktree_path / "scratch.txt").write_text("x", encoding="utf-8")
    result = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "dirty-worktree" for c in result.data["conflicts"])


def test_active_task_ownership_refused(git_repo):
    _create(git_repo)
    registry_path = git_repo / ".agent" / "runtime" / "WORKTREE_REGISTRY.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["records"][0]["task_id"] = "task-1"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    result = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "active-task-ownership" for c in result.data["conflicts"])


# --- successful removal --------------------------------------------------------


def test_confirmed_removal_succeeds(git_repo):
    created = _create(git_repo)
    worktree_path = Path(created.data["worktree_path"])
    result = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["action"] == "removed_worktree"
    assert not worktree_path.exists()


def test_removal_updates_registry(git_repo):
    _create(git_repo)
    run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    registry = load_registry(git_repo)
    assert registry.records[0].status == "removed"
    listing = run_worktree_list(str(git_repo), write_log=False)
    assert all(w["forgeops_registered"] is False for w in listing.data["worktrees"])


def test_branch_preserved_by_default(git_repo):
    created = _create(git_repo)
    branch = created.data["branch"]
    run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    out = _git(["branch", "--list", branch], cwd=git_repo).stdout
    assert branch in out


def test_source_checkout_unchanged(git_repo):
    before = (git_repo / "README.md").read_text(encoding="utf-8")
    _create(git_repo)
    run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    assert (git_repo / "README.md").read_text(encoding="utf-8") == before


def test_spacey_paths(spacey_git_repo):
    created = _create(spacey_git_repo)
    worktree_path = Path(created.data["worktree_path"])
    result = run_worktree_remove("demo", str(spacey_git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.SUCCESS
    assert not worktree_path.exists()


def test_idempotent_followup_reports_not_found(git_repo):
    _create(git_repo)
    first = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    assert first.exit_code == exit_codes.SUCCESS
    second = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    assert second.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "not-registered" for c in second.data["conflicts"])


# --- branch deletion ------------------------------------------------------------


def test_delete_branch_requires_confirm(git_repo):
    _create(git_repo)
    before = set(git_repo.parent.rglob("*"))
    result = run_worktree_remove("demo", str(git_repo), write_log=False, delete_branch=True)
    after = set(git_repo.parent.rglob("*"))
    assert result.exit_code == exit_codes.BLOCKED
    assert before == after


def test_forgeops_branch_deleted_with_confirm(git_repo):
    created = _create(git_repo)
    branch = created.data["branch"]
    result = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True, delete_branch=True)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["branch_deletion"]["deleted"] is True
    out = _git(["branch", "--list", branch], cwd=git_repo).stdout
    assert branch not in out


def test_no_deletion_without_delete_branch_flag(git_repo):
    created = _create(git_repo)
    branch = created.data["branch"]
    run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    out = _git(["branch", "--list", branch], cwd=git_repo).stdout
    assert branch in out


def test_non_forgeops_branch_refused(git_repo):
    created = _create(git_repo, branch="custom/mine")
    result = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True, delete_branch=True)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    assert result.data["branch_deletion"]["deleted"] is False
    out = _git(["branch", "--list", "custom/mine"], cwd=git_repo).stdout
    assert "custom/mine" in out


def test_unmerged_branch_deletion_refusal_leaves_branch_intact(git_repo):
    created = _create(git_repo)
    branch = created.data["branch"]
    worktree_path = Path(created.data["worktree_path"])
    (worktree_path / "extra.txt").write_text("x", encoding="utf-8")
    _git(["add", "extra.txt"], cwd=worktree_path)
    _git(["commit", "-m", "unmerged work"], cwd=worktree_path)
    result = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True, delete_branch=True)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    assert result.data["action"] == "removed_worktree"
    assert result.data["branch_deletion"]["deleted"] is False
    out = _git(["branch", "--list", branch], cwd=git_repo).stdout
    assert branch in out


def test_no_remote_deletion_no_force(git_repo):
    _create(git_repo)
    remotes_before = _git(["remote", "-v"], cwd=git_repo).stdout
    run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True, delete_branch=True)
    remotes_after = _git(["remote", "-v"], cwd=git_repo).stdout
    assert remotes_before == remotes_after == ""


# --- failure handling ------------------------------------------------------------


def test_simulated_git_removal_failure_reports_partial_state(git_repo, monkeypatch):
    from forgeops.state.worktree_remove import WorktreeRemoveOutcome

    _create(git_repo)
    monkeypatch.setattr(
        "forgeops.cli.worktree.apply_worktree_remove",
        lambda repo_root, plan: WorktreeRemoveOutcome(
            ok=False, stdout="", stderr="fatal: simulated failure",
            git_no_longer_lists_it=False, directory_removed=False,
            partial_state={"still_listed_in_git": True, "directory_exists": True},
            registry_written=False, registry_error=None, branch_deletion=None,
        ),
    )
    result = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    assert result.data["partial_state"] is not None
    assert "manual_recovery_recommendation" in result.data


def test_credentials_never_leaked_in_output(git_repo, monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "leaked-secret-value-98765")  # forgeops:allow-secret
    _create(git_repo)
    result = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    assert "leaked-secret-value-98765" not in result.to_json()
    assert "leaked-secret-value-98765" not in render_human(result)


def test_no_git_executable_returns_command_execution_failure(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.worktree.git_version", lambda: None)
    result = run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE


def test_internal_error_path_not_swallowed_by_run_function(git_repo, monkeypatch):
    import pytest

    _create(git_repo)

    def boom(repo_root, name_arg, delete_branch=False):
        raise RuntimeError("simulated internal bug")

    monkeypatch.setattr("forgeops.cli.worktree.build_worktree_remove_plan", boom)
    with pytest.raises(RuntimeError):
        run_worktree_remove("demo", str(git_repo), write_log=False, confirm=True)


# --- TrendForge / protected target ----------------------------------------------


def test_trendforge_target_is_rejected(tmp_path, monkeypatch):
    fake_reference = tmp_path / "FakeTrendForge"
    fake_reference.mkdir()
    _git(["init", "-q"], cwd=fake_reference)
    monkeypatch.setattr("forgeops.core.paths.READONLY_REFERENCE_REPO", fake_reference)
    result = run_worktree_remove("demo", str(fake_reference), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED


# --- regression: other worktree/CLI commands unaffected -------------------------


def test_worktree_list_still_works(git_repo):
    _create(git_repo)
    result = run_worktree_list(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


def test_worktree_create_still_works(git_repo):
    result = run_worktree_create("other", str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


def test_existing_status_command_still_works(git_repo):
    from forgeops.cli.status import run_status
    result = run_status(str(git_repo))
    assert result.exit_code == exit_codes.SUCCESS
