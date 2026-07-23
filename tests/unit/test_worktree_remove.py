"""Tests for forgeops/state/worktree_remove.py: the read-only preflight
plan builder and the single mutating apply step behind `forgeops
worktree remove`."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from forgeops.state.worktree_create import apply_worktree_create, build_worktree_create_plan
from forgeops.state.worktree_registry import STATUS_REMOVED, WorktreeRecord, WorktreeRegistryDocument, load_registry, save_registry
from forgeops.state.worktree_remove import apply_worktree_remove, build_worktree_remove_plan
from forgeops.worktrees.naming import worktree_path_for


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _create(git_repo: Path, name: str = "demo"):
    plan = build_worktree_create_plan(git_repo, name, None, None)
    outcome = apply_worktree_create(git_repo, plan)
    assert outcome.ok is True
    return plan.worktree_path, plan.branch


# --- preflight: clean plan -----------------------------------------------


def test_clean_plan_has_no_conflicts(git_repo):
    worktree_path, branch = _create(git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo", delete_branch=False)
    assert plan.has_conflict is False
    assert plan.worktree_path == worktree_path
    assert plan.branch == branch
    assert plan.record is not None
    assert plan.is_primary is False
    assert plan.locked is False


def test_build_plan_never_writes_anything(git_repo):
    _create(git_repo)
    before = set(git_repo.parent.rglob("*"))
    build_worktree_remove_plan(git_repo, "demo")
    after = set(git_repo.parent.rglob("*"))
    assert before == after


def test_branch_delete_precheck_eligible_for_clean_forgeops_branch(git_repo):
    _create(git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo", delete_branch=True)
    assert plan.has_conflict is False
    assert plan.branch_delete.requested is True
    assert plan.branch_delete.eligible is True
    assert plan.branch_delete.expected_tip is not None


def test_branch_delete_precheck_ineligible_for_non_forgeops_branch(git_repo):
    plan_c = build_worktree_create_plan(git_repo, "demo", "custom/branch", None)
    apply_worktree_create(git_repo, plan_c)
    plan = build_worktree_remove_plan(git_repo, "demo", delete_branch=True)
    assert plan.has_conflict is False
    assert plan.branch_delete.eligible is False
    assert "not in the ForgeOps-owned namespace" in plan.branch_delete.reason


# --- preflight: eligibility conflicts -------------------------------------


def test_unknown_worktree_name_is_a_conflict(git_repo):
    plan = build_worktree_remove_plan(git_repo, "does-not-exist")
    assert any(c.key == "not-registered" for c in plan.conflicts)


def test_absolute_name_is_rejected(git_repo):
    plan = build_worktree_remove_plan(git_repo, str(git_repo.parent / "evil"))
    assert any(c.key == "invalid-name" for c in plan.conflicts)


def test_traversal_name_is_rejected(git_repo):
    plan = build_worktree_remove_plan(git_repo, "../escape")
    assert any(c.key == "invalid-name" for c in plan.conflicts)


def test_stale_registry_only_entry_is_a_conflict(git_repo):
    worktree_path, _ = _create(git_repo)
    _git(["worktree", "remove", "--force", str(worktree_path)], cwd=git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert any(c.key == "stale-registry-entry" for c in plan.conflicts)


def test_malformed_registry_is_a_conflict(git_repo):
    registry_path = git_repo / ".agent" / "runtime" / "WORKTREE_REGISTRY.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{ not valid json", encoding="utf-8")
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert any(c.key == "registry-malformed" for c in plan.conflicts)


def test_duplicate_registry_name_is_a_conflict(git_repo):
    _create(git_repo, "demo")
    registry = load_registry(git_repo)
    dup = WorktreeRecord(
        id="dup-id", name="demo", path=registry.records[0].path + "2",
        branch="forgeops/demo2", base_commit=registry.records[0].base_commit,
        created_at=registry.records[0].created_at,
    )
    save_registry(git_repo, WorktreeRegistryDocument(records=[*registry.records, dup]))
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert any(c.key == "duplicate-registry-entry" for c in plan.conflicts)


def test_primary_checkout_rejected(git_repo):
    result_path = worktree_path_for(git_repo, "demo")
    registry = WorktreeRegistryDocument(records=[WorktreeRecord(
        id="x", name="demo", path=str(git_repo), branch="forgeops/demo",
        base_commit="deadbeef", created_at="2026-01-01T00:00:00Z",
    )])
    save_registry(git_repo, registry)
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert any(c.key == "primary-checkout" for c in plan.conflicts)
    assert result_path != git_repo  # sanity: distinct from a real managed path


def test_path_outside_managed_root_is_a_conflict(git_repo):
    registry = WorktreeRegistryDocument(records=[WorktreeRecord(
        id="x", name="demo", path=str(git_repo.parent / "somewhere-else"), branch="forgeops/demo",
        base_commit="deadbeef", created_at="2026-01-01T00:00:00Z",
    )])
    save_registry(git_repo, registry)
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert plan.has_conflict
    assert any(c.key in ("registry-path-mismatch", "outside-managed-root", "not-registered", "stale-registry-entry") for c in plan.conflicts)


def test_locked_worktree_is_a_conflict(git_repo):
    worktree_path, _ = _create(git_repo)
    _git(["worktree", "lock", "--reason", "manual test lock", str(worktree_path)], cwd=git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert any(c.key == "worktree-locked" for c in plan.conflicts)


def test_detached_registered_worktree_is_identity_mismatch(git_repo):
    worktree_path, branch = _create(git_repo)
    head = _git(["rev-parse", "HEAD"], cwd=worktree_path).stdout.strip()
    _git(["checkout", "--detach", head], cwd=worktree_path)
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert any(c.key == "identity-mismatch" for c in plan.conflicts)


def test_active_task_ownership_is_a_conflict(git_repo):
    _create(git_repo)
    registry = load_registry(git_repo)
    owned = [
        WorktreeRecord(**{**r.to_dict(), "task_id": "task-123"})
        for r in registry.records
    ]
    save_registry(git_repo, WorktreeRegistryDocument(records=owned))
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert any(c.key == "active-task-ownership" for c in plan.conflicts)


def test_active_agent_ownership_is_a_conflict(git_repo):
    _create(git_repo)
    registry = load_registry(git_repo)
    owned = [
        WorktreeRecord(**{**r.to_dict(), "agent_id": "agent-abc"})
        for r in registry.records
    ]
    save_registry(git_repo, WorktreeRegistryDocument(records=owned))
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert any(c.key == "active-agent-ownership" for c in plan.conflicts)


# --- preflight: dirty / busy -----------------------------------------------


def test_tracked_modification_is_a_conflict(git_repo):
    worktree_path, _ = _create(git_repo)
    (worktree_path / "README.md").write_text("changed", encoding="utf-8")
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert any(c.key == "dirty-worktree" for c in plan.conflicts)


def test_staged_change_is_a_conflict(git_repo):
    worktree_path, _ = _create(git_repo)
    (worktree_path / "new.txt").write_text("x", encoding="utf-8")
    _git(["add", "new.txt"], cwd=worktree_path)
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert any(c.key == "dirty-worktree" for c in plan.conflicts)


def test_untracked_file_is_a_conflict(git_repo):
    worktree_path, _ = _create(git_repo)
    (worktree_path / "scratch.txt").write_text("x", encoding="utf-8")
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert any(c.key == "dirty-worktree" for c in plan.conflicts)


def test_merge_in_progress_is_a_conflict(git_repo):
    worktree_path, _ = _create(git_repo)
    (worktree_path / "conflict.txt").write_text("base", encoding="utf-8")
    _git(["add", "conflict.txt"], cwd=worktree_path)
    _git(["commit", "-m", "base"], cwd=worktree_path)
    _git(["checkout", "-b", "side"], cwd=worktree_path)
    (worktree_path / "conflict.txt").write_text("side", encoding="utf-8")
    _git(["commit", "-am", "side"], cwd=worktree_path)
    _git(["checkout", "forgeops/demo"], cwd=worktree_path)
    (worktree_path / "conflict.txt").write_text("main", encoding="utf-8")
    _git(["commit", "-am", "main"], cwd=worktree_path)
    subprocess.run(["git", "merge", "side"], cwd=str(worktree_path), capture_output=True, text=True)
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert any(c.key in ("dirty-worktree", "git-operation-in-progress") for c in plan.conflicts)


def test_no_process_termination_side_effect(git_repo):
    # Sanity: preflight against a plain worktree never touches any process.
    worktree_path, _ = _create(git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert plan.has_conflict is False


# --- apply: success ---------------------------------------------------------


def test_apply_removes_worktree_and_marks_registry_removed(git_repo):
    worktree_path, _ = _create(git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo")
    assert not plan.has_conflict
    outcome = apply_worktree_remove(git_repo, plan)
    assert outcome.ok is True
    assert not worktree_path.exists()
    registry = load_registry(git_repo)
    assert len(registry.records) == 1
    assert registry.records[0].status == STATUS_REMOVED


def test_apply_preserves_branch_by_default(git_repo):
    worktree_path, branch = _create(git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo", delete_branch=False)
    outcome = apply_worktree_remove(git_repo, plan)
    assert outcome.ok is True
    assert outcome.branch_deletion is None
    out = _git(["branch", "--list", branch], cwd=git_repo).stdout
    assert branch in out


def test_apply_deletes_branch_when_requested(git_repo):
    worktree_path, branch = _create(git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo", delete_branch=True)
    assert plan.branch_delete.eligible is True
    outcome = apply_worktree_remove(git_repo, plan)
    assert outcome.ok is True
    assert outcome.branch_deletion.attempted is True
    assert outcome.branch_deletion.deleted is True
    out = _git(["branch", "--list", branch], cwd=git_repo).stdout
    assert branch not in out


def test_apply_source_checkout_unchanged(git_repo):
    before = (git_repo / "README.md").read_text(encoding="utf-8")
    _create(git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo")
    apply_worktree_remove(git_repo, plan)
    assert (git_repo / "README.md").read_text(encoding="utf-8") == before


def test_apply_spacey_paths(spacey_git_repo):
    plan_c = build_worktree_create_plan(spacey_git_repo, "demo", None, None)
    apply_worktree_create(spacey_git_repo, plan_c)
    plan = build_worktree_remove_plan(spacey_git_repo, "demo")
    assert not plan.has_conflict
    outcome = apply_worktree_remove(spacey_git_repo, plan)
    assert outcome.ok is True
    assert not plan_c.worktree_path.exists()


def test_apply_idempotent_followup_is_not_registered(git_repo):
    _create(git_repo)
    plan1 = build_worktree_remove_plan(git_repo, "demo")
    apply_worktree_remove(git_repo, plan1)
    plan2 = build_worktree_remove_plan(git_repo, "demo")
    assert any(c.key == "not-registered" for c in plan2.conflicts)


# --- branch deletion: refusal paths -----------------------------------------


def test_non_forgeops_branch_never_deleted(git_repo):
    plan_c = build_worktree_create_plan(git_repo, "demo", "custom/branch-name", None)
    apply_worktree_create(git_repo, plan_c)
    plan = build_worktree_remove_plan(git_repo, "demo", delete_branch=True)
    assert plan.branch_delete.eligible is False
    outcome = apply_worktree_remove(git_repo, plan)
    assert outcome.ok is True
    assert outcome.branch_deletion.attempted is False
    out = _git(["branch", "--list", "custom/branch-name"], cwd=git_repo).stdout
    assert "custom/branch-name" in out


def test_unmerged_branch_deletion_refused_leaves_branch_intact(git_repo):
    worktree_path, branch = _create(git_repo)
    (worktree_path / "extra.txt").write_text("x", encoding="utf-8")
    _git(["add", "extra.txt"], cwd=worktree_path)
    _git(["commit", "-m", "extra work not merged to main"], cwd=worktree_path)
    plan = build_worktree_remove_plan(git_repo, "demo", delete_branch=True)
    assert not plan.has_conflict
    outcome = apply_worktree_remove(git_repo, plan)
    assert outcome.ok is True  # worktree removal itself still succeeds
    assert outcome.branch_deletion.attempted is True
    assert outcome.branch_deletion.deleted is False
    out = _git(["branch", "--list", branch], cwd=git_repo).stdout
    assert branch in out  # never force-deleted


def test_branch_checked_out_elsewhere_not_deleted(git_repo, monkeypatch):
    # Git itself never allows the same branch to be checked out in two
    # worktrees at once, so this path is defensive rather than reachable
    # through normal git operations - exercised directly against the
    # plan builder by monkeypatching list_worktrees, the same pattern
    # used for worktree create's own unreachable-via-CLI conflict paths.
    worktree_path, branch = _create(git_repo)
    from forgeops.worktrees.git_worktree import WorktreeEntry, WorktreeListResult

    real_list_worktrees = __import__("forgeops.worktrees.git_worktree", fromlist=["list_worktrees"]).list_worktrees

    def fake_list_worktrees(repo_root):
        real = real_list_worktrees(repo_root)
        fake_elsewhere = WorktreeEntry(
            path=str(worktree_path) + "-elsewhere", head="deadbeef", branch=branch,
            detached=False, bare=False, locked=False, locked_reason=None,
            prunable=False, prunable_reason=None,
        )
        return WorktreeListResult(ok=True, entries=[*real.entries, fake_elsewhere], warnings=[])

    monkeypatch.setattr("forgeops.state.worktree_remove.list_worktrees", fake_list_worktrees)
    plan = build_worktree_remove_plan(git_repo, "demo", delete_branch=True)
    assert plan.branch_delete.eligible is False
    assert "checked out in another worktree" in plan.branch_delete.reason


# --- apply: failure / TOCTOU -------------------------------------------------


def test_apply_reports_simulated_git_failure_without_raising(git_repo, monkeypatch):
    from forgeops.core.subprocess_utils import ProcResult

    _create(git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo")

    def fake_remove_worktree(repo_root, worktree_path):
        return ProcResult(args=(), returncode=128, stdout="", stderr="fatal: simulated failure", timed_out=False, error=None)

    monkeypatch.setattr("forgeops.state.worktree_remove.remove_worktree", fake_remove_worktree)
    outcome = apply_worktree_remove(git_repo, plan)
    assert outcome.ok is False
    assert "simulated failure" in outcome.stderr
    assert outcome.registry_written is False
    registry = load_registry(git_repo)
    assert registry.records[0].status == "active"


def test_apply_failed_git_removal_never_touches_registry(git_repo, monkeypatch):
    from forgeops.core.subprocess_utils import ProcResult

    _create(git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo")
    monkeypatch.setattr(
        "forgeops.state.worktree_remove.remove_worktree",
        lambda *a, **k: ProcResult(args=(), returncode=1, stdout="", stderr="boom", timed_out=False, error=None),
    )
    apply_worktree_remove(git_repo, plan)
    registry = load_registry(git_repo)
    assert registry.records[0].status == "active"


def test_apply_git_reports_success_but_still_listed_is_reported_partial(git_repo, monkeypatch):
    # Simulates "git worktree remove reported success but the directory/
    # registration didn't actually go away" by faking a zero-returncode
    # git call that performs no real removal - the real (unpatched)
    # post-removal `git worktree list` then still shows the entry, which
    # must be reported as a partial failure, never a false success.
    worktree_path, _ = _create(git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo")

    from forgeops.core.subprocess_utils import ProcResult

    monkeypatch.setattr(
        "forgeops.state.worktree_remove.remove_worktree",
        lambda repo_root, wt_path: ProcResult(args=(), returncode=0, stdout="", stderr="", timed_out=False, error=None),
    )
    outcome = apply_worktree_remove(git_repo, plan)
    assert outcome.ok is False
    assert outcome.partial_state is not None
    assert outcome.registry_written is False
    assert worktree_path.exists()
    registry = load_registry(git_repo)
    assert registry.records[0].status == "active"


def test_apply_registry_write_failure_reported_but_removal_still_ok(git_repo, monkeypatch):
    _create(git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo")
    monkeypatch.setattr(
        "forgeops.state.worktree_remove.save_registry",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_worktree_remove(git_repo, plan)
    assert outcome.ok is True
    assert outcome.registry_written is False
    assert outcome.registry_error is not None


def test_apply_identity_changed_since_preflight_is_refused(git_repo):
    worktree_path, branch = _create(git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo")
    # Reality changes between preflight and apply: someone detaches HEAD.
    head = _git(["rev-parse", "HEAD"], cwd=worktree_path).stdout.strip()
    _git(["checkout", "--detach", head], cwd=worktree_path)
    outcome = apply_worktree_remove(git_repo, plan)
    assert outcome.ok is False
    assert outcome.registry_written is False
    assert worktree_path.exists()


def test_apply_branch_tip_changed_since_preflight_skips_deletion(git_repo):
    worktree_path, branch = _create(git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo", delete_branch=True)
    assert plan.branch_delete.eligible is True
    (worktree_path / "extra.txt").write_text("x", encoding="utf-8")
    _git(["add", "extra.txt"], cwd=worktree_path)
    _git(["commit", "-m", "moved after preflight"], cwd=worktree_path)
    outcome = apply_worktree_remove(git_repo, plan)
    # Identity revalidation compares entry.branch (still matches), but the
    # branch tip has moved - branch deletion must be skipped, not the
    # worktree removal itself (which is keyed on path/branch identity,
    # not commit SHA).
    assert outcome.ok is True
    assert outcome.branch_deletion.attempted is False
    assert "changed since preflight" in outcome.branch_deletion.reason


def test_apply_credentials_never_leaked(git_repo, monkeypatch):
    from forgeops.core.subprocess_utils import ProcResult

    _create(git_repo)
    plan = build_worktree_remove_plan(git_repo, "demo")
    monkeypatch.setattr(
        "forgeops.state.worktree_remove.remove_worktree",
        lambda *a, **k: ProcResult(
            args=(), returncode=1, stdout="",
            stderr="fatal: https://user:super-secret-leak@example.invalid/repo.git failed",
            timed_out=False, error=None,
        ),
    )
    outcome = apply_worktree_remove(git_repo, plan)
    assert "super-secret-leak" not in outcome.stderr
