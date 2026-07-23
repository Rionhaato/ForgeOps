"""Tests for forgeops/state/project_init.py: the pure preflight-plan
builder, content renderers, and the rollback-on-failure atomic writer
behind `forgeops init` (forgeops/cli/init.py)."""
from __future__ import annotations

import json

from forgeops.state.project_init import (
    FORGEOPS_MANAGED_MARKER,
    apply_init_plan,
    build_init_plan,
    build_initial_current_state,
)


def test_fresh_directory_plans_everything_as_missing(tmp_path):
    plan = build_init_plan(tmp_path)
    assert not plan.has_conflict
    assert not plan.is_git_repo
    assert plan.branch is None
    states = {p.key: p.state for p in plan.paths}
    assert set(states.values()) == {"missing"}
    assert set(states.keys()) == {
        ".agent", "current_state", "project_facts", "decisions", "handoff", "claude_md",
    }


def test_git_repo_directory_reports_branch_when_available(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    from forgeops.core.git import BranchState
    monkeypatch.setattr(
        "forgeops.state.project_init.get_branch_state",
        lambda repo_root: BranchState(branch="main", detached=False, has_commits=True),
    )
    plan = build_init_plan(tmp_path)
    assert plan.is_git_repo is True
    assert plan.branch == "main"


def test_non_git_directory_never_shells_out_to_git(tmp_path, monkeypatch):
    monkeypatch.setattr("forgeops.state.project_init.get_branch_state", lambda repo_root: (_ for _ in ()).throw(AssertionError("must not call git for a non-git directory")))
    plan = build_init_plan(tmp_path)
    assert plan.is_git_repo is False
    assert plan.branch is None


def test_existing_current_state_with_supported_schema_is_compatible(tmp_path):
    state_dir = tmp_path / ".agent"
    state_dir.mkdir()
    (state_dir / "CURRENT_STATE.json").write_text(
        json.dumps({"schema_version": 1, "branch": "x", "mission": "m", "completed_work": [], "blockers": [], "next_action": "n"}),
        encoding="utf-8",
    )
    plan = build_init_plan(tmp_path)
    by_key = {p.key: p for p in plan.paths}
    assert by_key["current_state"].state == "compatible"
    assert by_key[".agent"].state == "compatible"


def test_existing_current_state_malformed_json_is_a_conflict(tmp_path):
    state_dir = tmp_path / ".agent"
    state_dir.mkdir()
    (state_dir / "CURRENT_STATE.json").write_text("{not valid json", encoding="utf-8")
    plan = build_init_plan(tmp_path)
    by_key = {p.key: p for p in plan.paths}
    assert by_key["current_state"].state == "conflict"
    assert plan.has_conflict


def test_existing_current_state_unsupported_schema_version_is_a_conflict(tmp_path):
    state_dir = tmp_path / ".agent"
    state_dir.mkdir()
    (state_dir / "CURRENT_STATE.json").write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
    plan = build_init_plan(tmp_path)
    by_key = {p.key: p for p in plan.paths}
    assert by_key["current_state"].state == "conflict"


def test_existing_managed_markdown_with_marker_is_compatible(tmp_path):
    (tmp_path / "CLAUDE.md").write_text(f"{FORGEOPS_MANAGED_MARKER}\n\n# CLAUDE.md\n", encoding="utf-8")
    plan = build_init_plan(tmp_path)
    by_key = {p.key: p for p in plan.paths}
    assert by_key["claude_md"].state == "compatible"


def test_existing_user_owned_claude_md_without_marker_is_a_conflict(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# My own rules\n", encoding="utf-8")
    plan = build_init_plan(tmp_path)
    by_key = {p.key: p for p in plan.paths}
    assert by_key["claude_md"].state == "conflict"
    assert plan.has_conflict


def test_agent_path_is_a_file_is_a_conflict(tmp_path):
    (tmp_path / ".agent").write_text("oops, a file not a directory", encoding="utf-8")
    plan = build_init_plan(tmp_path)
    by_key = {p.key: p for p in plan.paths}
    assert by_key[".agent"].state == "conflict"
    assert plan.has_conflict


def test_build_initial_current_state_has_required_keys_and_schema(tmp_path):
    document = build_initial_current_state(tmp_path)
    assert document["schema_version"] == 1
    for key in ("branch", "mission", "completed_work", "blockers", "next_action"):
        assert key in document
    assert document["mission"] == "Project initialized under ForgeOps governance."
    assert document["repository"] == {"name": tmp_path.name, "root": str(tmp_path)}


def test_apply_init_plan_creates_all_missing_paths(tmp_path):
    plan = build_init_plan(tmp_path)
    outcomes, error = apply_init_plan(tmp_path, plan)
    assert error is None
    assert all(o.ok for o in outcomes)
    assert (tmp_path / ".agent").is_dir()
    assert (tmp_path / ".agent" / "CURRENT_STATE.json").is_file()
    assert (tmp_path / ".agent" / "PROJECT_FACTS.md").is_file()
    assert (tmp_path / ".agent" / "DECISIONS.md").is_file()
    assert (tmp_path / ".agent" / "HANDOFF.md").is_file()
    assert (tmp_path / "CLAUDE.md").is_file()
    # No speculative empty directories beyond .agent itself.
    assert not (tmp_path / ".agent" / "runtime").exists()
    assert not (tmp_path / ".agent" / "checkpoints").exists()
    assert not (tmp_path / ".agent" / "logs").exists()


def test_apply_init_plan_preserves_existing_compatible_paths(tmp_path):
    plan1 = build_init_plan(tmp_path)
    apply_init_plan(tmp_path, plan1)
    original_facts = (tmp_path / ".agent" / "PROJECT_FACTS.md").read_text(encoding="utf-8")

    plan2 = build_init_plan(tmp_path)
    outcomes, error = apply_init_plan(tmp_path, plan2)
    assert error is None
    assert all(o.action == "preserved" for o in outcomes)
    assert (tmp_path / ".agent" / "PROJECT_FACTS.md").read_text(encoding="utf-8") == original_facts


def test_apply_init_plan_rolls_back_new_paths_on_write_failure(tmp_path, monkeypatch):
    plan = build_init_plan(tmp_path)

    call_count = {"n": 0}
    real_atomic_write_text = __import__("forgeops.state.atomic_write", fromlist=["atomic_write_text"]).atomic_write_text

    def flaky_write(path, content, encoding="utf-8"):
        call_count["n"] += 1
        if call_count["n"] >= 3:
            raise OSError("simulated disk full")
        real_atomic_write_text(path, content, encoding=encoding)

    monkeypatch.setattr("forgeops.state.project_init.atomic_write_text", flaky_write)
    outcomes, error = apply_init_plan(tmp_path, plan)
    assert error is not None

    # Nothing this run created may survive a failure partway through.
    assert not (tmp_path / ".agent").exists()
    assert not (tmp_path / "CLAUDE.md").exists()


def test_apply_init_plan_never_rolls_back_preexisting_compatible_paths(tmp_path, monkeypatch):
    plan1 = build_init_plan(tmp_path)
    apply_init_plan(tmp_path, plan1)  # first, clean init

    # Simulate a second run where a later write fails - the files preserved
    # from the first run must survive even though this run's own new
    # writes get rolled back. Nothing is "missing" on this second plan
    # though, so force one path back to missing to exercise a real write.
    (tmp_path / ".agent" / "HANDOFF.md").unlink()
    plan2 = build_init_plan(tmp_path)
    by_key = {p.key: p for p in plan2.paths}
    assert by_key["handoff"].state == "missing"
    assert by_key["current_state"].state == "compatible"

    def boom(path, content, encoding="utf-8"):
        raise OSError("simulated disk full")

    monkeypatch.setattr("forgeops.state.project_init.atomic_write_text", boom)
    outcomes, error = apply_init_plan(tmp_path, plan2)
    assert error is not None
    # The preserved CURRENT_STATE.json/PROJECT_FACTS.md/DECISIONS.md/CLAUDE.md from run 1 must still exist.
    assert (tmp_path / ".agent" / "CURRENT_STATE.json").is_file()
    assert (tmp_path / ".agent" / "PROJECT_FACTS.md").is_file()
    assert (tmp_path / ".agent" / "DECISIONS.md").is_file()
    assert (tmp_path / "CLAUDE.md").is_file()
    # But the one this run tried (and failed) to recreate must not exist.
    assert not (tmp_path / ".agent" / "HANDOFF.md").exists()
