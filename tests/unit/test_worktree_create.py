"""Tests for forgeops/state/worktree_create.py: the read-only preflight
plan builder and the single mutating apply step behind `forgeops
worktree create`."""
from __future__ import annotations

import subprocess
from pathlib import Path

from forgeops.state.worktree_create import apply_worktree_create, build_worktree_create_plan
from forgeops.state.worktree_registry import load_registry
from forgeops.worktrees.naming import managed_root_for, worktree_path_for


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _head(repo_root: Path) -> str:
    return _git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip()


# --- preflight: clean plan ---------------------------------------------------


def test_clean_plan_has_no_conflicts_and_derives_branch(git_repo):
    plan = build_worktree_create_plan(git_repo, "demo", None, None)
    assert plan.has_conflict is False
    assert plan.sanitized_name == "demo"
    assert plan.branch == "forgeops/demo"
    assert plan.branch_source == "derived"
    assert plan.base_ref == "HEAD"
    assert plan.base_commit == _head(git_repo)
    assert plan.worktree_path == worktree_path_for(git_repo, "demo")


def test_explicit_branch_is_used_verbatim(git_repo):
    plan = build_worktree_create_plan(git_repo, "demo", "my-custom-branch", None)
    assert plan.branch == "my-custom-branch"
    assert plan.branch_source == "explicit"
    assert plan.has_conflict is False


def test_explicit_base_ref_is_resolved(git_repo):
    _git(["tag", "v1"], cwd=git_repo)
    plan = build_worktree_create_plan(git_repo, "demo", None, "v1")
    assert plan.base_ref == "v1"
    assert plan.base_commit == _head(git_repo)


def test_build_plan_never_writes_anything(git_repo):
    before = set(git_repo.rglob("*"))
    build_worktree_create_plan(git_repo, "demo", None, None)
    after = set(git_repo.rglob("*"))
    assert before == after


# --- preflight: conflicts -----------------------------------------------------


def test_invalid_name_is_a_conflict(git_repo):
    plan = build_worktree_create_plan(git_repo, "..", None, None)
    assert plan.has_conflict
    assert any(c.key == "invalid-name" for c in plan.conflicts)
    assert plan.worktree_path is None


def test_missing_base_ref_is_a_conflict(git_repo):
    plan = build_worktree_create_plan(git_repo, "demo", None, "does-not-exist-ref")
    assert plan.has_conflict
    assert any(c.key == "base-ref-not-found" for c in plan.conflicts)


def test_existing_destination_is_a_conflict(git_repo):
    dest = worktree_path_for(git_repo, "demo")
    dest.mkdir(parents=True)
    plan = build_worktree_create_plan(git_repo, "demo", None, None)
    assert any(c.key == "destination-exists" for c in plan.conflicts)


def test_duplicate_worktree_is_a_conflict(git_repo, tmp_path):
    managed = managed_root_for(git_repo)
    managed.mkdir(parents=True)
    dest = managed / "demo"
    _git(["worktree", "add", "-b", "forgeops/demo", str(dest)], cwd=git_repo)
    plan = build_worktree_create_plan(git_repo, "demo", None, None)
    assert any(c.key in ("duplicate-worktree", "destination-exists") for c in plan.conflicts)


def test_branch_already_exists_is_a_conflict(git_repo):
    _git(["branch", "forgeops/demo"], cwd=git_repo)
    plan = build_worktree_create_plan(git_repo, "demo", None, None)
    assert any(c.key == "branch-already-exists" for c in plan.conflicts)


def test_branch_checked_out_elsewhere_is_a_conflict(git_repo, tmp_path):
    other = tmp_path / "other-checkout"
    _git(["worktree", "add", "-b", "forgeops/demo", str(other)], cwd=git_repo)
    plan = build_worktree_create_plan(git_repo, "demo2", "forgeops/demo", None)
    assert any(c.key == "branch-checked-out-elsewhere" for c in plan.conflicts)


def test_malformed_registry_is_a_conflict(git_repo):
    registry_path = git_repo / ".agent" / "runtime" / "WORKTREE_REGISTRY.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{ not valid json", encoding="utf-8")
    plan = build_worktree_create_plan(git_repo, "demo", None, None)
    assert any(c.key == "registry-malformed" for c in plan.conflicts)


def test_bare_repository_is_a_conflict(tmp_path):
    bare = tmp_path / "bare.git"
    _git(["init", "-q", "--bare", str(bare)], cwd=tmp_path)
    plan = build_worktree_create_plan(bare, "demo", None, None)
    assert any(c.key == "bare-repository" for c in plan.conflicts)


def test_protected_reference_repository_is_a_conflict(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.state.worktree_create.is_protected_reference_path", lambda p: True)
    plan = build_worktree_create_plan(git_repo, "demo", None, None)
    assert any(c.key == "protected-reference-repo" for c in plan.conflicts)


# --- apply: success -----------------------------------------------------------


def test_apply_creates_worktree_and_registry_entry(git_repo):
    plan = build_worktree_create_plan(git_repo, "demo", None, None)
    assert not plan.has_conflict
    outcome = apply_worktree_create(git_repo, plan)
    assert outcome.ok is True
    assert plan.worktree_path.is_dir()
    assert outcome.registry_written is True
    registry = load_registry(git_repo)
    assert len(registry.records) == 1
    assert registry.records[0].name == "demo"
    assert registry.records[0].branch == "forgeops/demo"
    assert registry.records[0].base_commit == plan.base_commit


def test_apply_never_writes_outside_managed_root(git_repo):
    plan = build_worktree_create_plan(git_repo, "demo", None, None)
    outcome = apply_worktree_create(git_repo, plan)
    assert outcome.ok is True
    assert plan.worktree_path.is_relative_to(managed_root_for(git_repo))


def test_apply_does_not_modify_source_checkout_tracked_files(git_repo):
    tracked_before = (git_repo / "README.md").read_text(encoding="utf-8")
    plan = build_worktree_create_plan(git_repo, "demo", None, None)
    apply_worktree_create(git_repo, plan)
    assert (git_repo / "README.md").read_text(encoding="utf-8") == tracked_before


def test_apply_spacey_repo_and_worktree_root(spacey_git_repo):
    plan = build_worktree_create_plan(spacey_git_repo, "demo", None, None)
    assert not plan.has_conflict
    outcome = apply_worktree_create(spacey_git_repo, plan)
    assert outcome.ok is True
    assert plan.worktree_path.is_dir()


# --- apply: simulated failure --------------------------------------------------


def test_apply_reports_simulated_git_failure_without_raising(git_repo, monkeypatch):
    from forgeops.core.subprocess_utils import ProcResult

    def fake_add_worktree(repo_root, worktree_path, branch, base_commit):
        return ProcResult(args=(), returncode=128, stdout="", stderr="fatal: simulated failure", timed_out=False, error=None)

    monkeypatch.setattr("forgeops.state.worktree_create.add_worktree", fake_add_worktree)
    plan = build_worktree_create_plan(git_repo, "demo", None, None)
    outcome = apply_worktree_create(git_repo, plan)
    assert outcome.ok is False
    assert "simulated failure" in outcome.stderr
    assert outcome.partial_state is not None
    assert outcome.partial_state["directory_created"] is False
    assert outcome.partial_state["branch_created"] is False
    assert outcome.registry_written is False


def test_apply_failure_never_writes_registry(git_repo, monkeypatch):
    from forgeops.core.subprocess_utils import ProcResult

    monkeypatch.setattr(
        "forgeops.state.worktree_create.add_worktree",
        lambda *a, **k: ProcResult(args=(), returncode=1, stdout="", stderr="boom", timed_out=False, error=None),
    )
    plan = build_worktree_create_plan(git_repo, "demo", None, None)
    apply_worktree_create(git_repo, plan)
    registry = load_registry(git_repo)
    assert registry.records == []


def test_apply_credentials_not_leaked_in_stderr(git_repo, monkeypatch):
    from forgeops.core.subprocess_utils import ProcResult

    monkeypatch.setattr(
        "forgeops.state.worktree_create.add_worktree",
        lambda *a, **k: ProcResult(
            args=(), returncode=1, stdout="",
            stderr="fatal: https://user:super-secret-leak@example.invalid/repo.git failed",
            timed_out=False, error=None,
        ),
    )
    plan = build_worktree_create_plan(git_repo, "demo", None, None)
    outcome = apply_worktree_create(git_repo, plan)
    assert "super-secret-leak" not in outcome.stderr
