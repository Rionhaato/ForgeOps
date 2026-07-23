"""Integration tests for `forgeops task assign`/`forgeops task unassign`
(forgeops/cli/task.py). See docs/tasks.md "Ownership" for the one-to-one
model, dry-run semantics, and confirmation model this exercises
end-to-end through the CLI entry points."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from forgeops.cli.task import (
    render_human,
    run_task_assign,
    run_task_close,
    run_task_create,
    run_task_list,
    run_task_show,
    run_task_unassign,
    run_task_validate,
)
from forgeops.cli.worktree import run_worktree_create
from forgeops.core import exit_codes
from forgeops.state.task_registry import (
    VALIDATION_STATUS_PASSED,
    load_task_record,
    load_validation_record,
    save_validation_record,
    task_dir_for,
)
from forgeops.state.worktree_registry import load_registry as load_worktree_registry


def _create_task(repo, title="My Task"):
    result = run_task_create(title, str(repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    return result.data["task_id"]


def _create_worktree(repo, name="demo"):
    result = run_worktree_create(name, str(repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    return name


def _fill_spec_and_close(repo, task_id, tmp_path):
    task_dir = task_dir_for(repo, task_id)
    (task_dir / "SPEC.md").write_text(
        "# SPEC\n\n## Objective\n\nfoo\n\n## In Scope\n\nfoo\n\n## Out of Scope\n\nfoo\n\n"
        "## Constraints\n\nfoo\n\n## Acceptance Criteria\n\n- works\n\n"
        "## Required Validation\n\n- pytest\n\n## Stop Boundary\n\nfoo\n",
        encoding="utf-8",
    )
    load = load_validation_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "status": VALIDATION_STATUS_PASSED})
    save_validation_record(task_dir, tampered)
    result_file = tmp_path / "result.md"
    result_file.write_text("## Outcome\n\ndone\n", encoding="utf-8")
    result = run_task_close(task_id, str(repo), write_log=False, confirm=True, result_file=str(result_file))
    assert result.exit_code == exit_codes.SUCCESS
    return result.data["new_status"]


# --- assignment ------------------------------------------------------------------


def test_assign_success(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    result = run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["action"] == "assigned"
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.worktree_id == name
    registry = load_worktree_registry(initialized_repo)
    assert registry.records[0].task_id == task_id


def test_assign_dry_run_performs_no_mutation(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    before = set(initialized_repo.rglob("*"))
    result = run_task_assign(task_id, name, str(initialized_repo), write_log=False, dry_run=True)
    after = set(initialized_repo.rglob("*"))
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["action"] == "would_assign"
    assert before == after


def test_assign_json_output(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    result = run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    payload = json.loads(result.to_json())
    assert payload["command"] == "task-assign"
    assert payload["data"]["worktree_name"] == name


def test_assign_human_output(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    result = run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    rendered = render_human(result)
    assert "forgeops task assign" in rendered
    assert task_id in rendered


def test_assign_duplicate_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    name1 = _create_worktree(initialized_repo, "demo1")
    name2 = _create_worktree(initialized_repo, "demo2")
    run_task_assign(task_id, name1, str(initialized_repo), write_log=False)
    result = run_task_assign(task_id, name2, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-already-assigned" for c in result.data["conflicts"])


def test_assign_assigned_worktree_refused(initialized_repo):
    task_id1 = _create_task(initialized_repo, "Task One")
    task_id2 = _create_task(initialized_repo, "Task Two")
    name = _create_worktree(initialized_repo)
    run_task_assign(task_id1, name, str(initialized_repo), write_log=False)
    result = run_task_assign(task_id2, name, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "worktree-already-assigned" for c in result.data["conflicts"])


def test_assign_assigned_task_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    name1 = _create_worktree(initialized_repo, "demo1")
    name2 = _create_worktree(initialized_repo, "demo2")
    run_task_assign(task_id, name1, str(initialized_repo), write_log=False)
    result = run_task_assign(task_id, name2, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-already-assigned" for c in result.data["conflicts"])


def test_assign_missing_task(initialized_repo):
    name = _create_worktree(initialized_repo)
    result = run_task_assign("task-9999", name, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-not-found" for c in result.data["conflicts"])


def test_assign_missing_worktree(initialized_repo):
    task_id = _create_task(initialized_repo)
    result = run_task_assign(task_id, "no-such-worktree", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "worktree-not-found" for c in result.data["conflicts"])


def test_assign_removed_worktree(initialized_repo):
    from forgeops.cli.worktree import run_worktree_remove
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    run_worktree_remove(name, str(initialized_repo), write_log=False, confirm=True)
    result = run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "worktree-removed" for c in result.data["conflicts"])


def test_assign_completed_task_refused(initialized_repo, tmp_path):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    _fill_spec_and_close(initialized_repo, task_id, tmp_path)
    result = run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-already-terminal" for c in result.data["conflicts"])


def test_assign_failed_task_refused(initialized_repo, tmp_path):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "SPEC.md").write_text(
        "# SPEC\n\n## Objective\n\nfoo\n\n## In Scope\n\nfoo\n\n## Out of Scope\n\nfoo\n\n"
        "## Constraints\n\nfoo\n\n## Acceptance Criteria\n\n- works\n\n"
        "## Required Validation\n\n- pytest\n\n## Stop Boundary\n\nfoo\n",
        encoding="utf-8",
    )
    from forgeops.state.task_registry import VALIDATION_STATUS_FAILED
    load = load_validation_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "status": VALIDATION_STATUS_FAILED})
    save_validation_record(task_dir, tampered)
    result_file = tmp_path / "result.md"
    result_file.write_text("## Outcome\n\nfailed\n", encoding="utf-8")
    run_task_close(task_id, str(initialized_repo), write_log=False, confirm=True, result_file=str(result_file))
    result = run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-already-terminal" for c in result.data["conflicts"])


def test_assign_cancelled_task_refused(initialized_repo):
    from forgeops.state.task_registry import STATUS_CANCELLED, save_task_record
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    record = load_task_record(task_dir).record
    tampered = type(record)(**{**record.__dict__, "status": STATUS_CANCELLED})
    save_task_record(task_dir, tampered)
    result = run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-already-terminal" for c in result.data["conflicts"])


def test_assign_registry_corruption(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    registry_path = initialized_repo / ".agent" / "runtime" / "WORKTREE_REGISTRY.json"
    registry_path.write_text("{ not valid", encoding="utf-8")
    result = run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "worktree-registry-malformed" for c in result.data["conflicts"])


def test_assign_schema_mismatch(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "TASK.json").write_text("{ not valid", encoding="utf-8")
    result = run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-json-malformed" for c in result.data["conflicts"])


def test_assign_ownership_mismatch_via_toctou(initialized_repo):
    task_id1 = _create_task(initialized_repo, "Task One")
    task_id2 = _create_task(initialized_repo, "Task Two")
    name = _create_worktree(initialized_repo)
    from forgeops.state.task_ownership import build_task_assign_plan
    plan = build_task_assign_plan(initialized_repo, task_id1, name)
    assert not plan.has_conflict
    run_task_assign(task_id2, name, str(initialized_repo), write_log=False)
    from forgeops.state.task_ownership import apply_task_assign
    outcome = apply_task_assign(initialized_repo, plan)
    assert outcome.ok is False


def test_assign_atomic_rollback(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    monkeypatch.setattr(
        "forgeops.state.task_ownership.save_worktree_registry",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    result = run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    assert result.data["partial_state"] is not None
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.worktree_id is None


def test_assign_partial_write_failure(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    monkeypatch.setattr(
        "forgeops.state.task_ownership.save_task_record",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    result = run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    assert "manual_recovery_recommendation" in result.data
    registry = load_worktree_registry(initialized_repo)
    assert registry.records[0].task_id is None


def test_assign_spacey_paths(spacey_initialized_repo):
    task_id = _create_task(spacey_initialized_repo)
    name = _create_worktree(spacey_initialized_repo)
    result = run_task_assign(task_id, name, str(spacey_initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


# --- unassign --------------------------------------------------------------------


def test_unassign_success(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    result = run_task_unassign(task_id, str(initialized_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.SUCCESS
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.worktree_id is None
    registry = load_worktree_registry(initialized_repo)
    assert registry.records[0].task_id is None


def test_unassign_dry_run_no_mutation(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    before = set(initialized_repo.rglob("*"))
    result = run_task_unassign(task_id, str(initialized_repo), write_log=False, dry_run=True)
    after = set(initialized_repo.rglob("*"))
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["action"] == "would_unassign"
    assert before == after


def test_unassign_confirmation_gate(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    before = set(initialized_repo.rglob("*"))
    result = run_task_unassign(task_id, str(initialized_repo), write_log=False)
    after = set(initialized_repo.rglob("*"))
    assert result.exit_code == exit_codes.BLOCKED
    assert result.data["action"] == "confirmation_required"
    assert before == after


def test_unassign_double_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    run_task_unassign(task_id, str(initialized_repo), write_log=False, confirm=True)
    result = run_task_unassign(task_id, str(initialized_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-not-assigned" for c in result.data["conflicts"])


def test_unassign_human_json_agree(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    result = run_task_unassign(task_id, str(initialized_repo), write_log=False, confirm=True)
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops task unassign" in rendered
    assert payload["data"]["current_worktree"] == name


# --- validate ownership consistency (CLI level) -----------------------------------


def test_validate_ownership_consistency(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    result = run_task_validate(task_id, str(initialized_repo), write_log=False)
    # Only the pre-existing placeholder blockers should remain - no ownership issue.
    ownership_keys = {"worktree-missing", "worktree-removed", "ownership-mismatch", "duplicate-assignment"}
    assert not any(b["key"] in ownership_keys for b in result.data["blockers"])


# --- show/list ownership output -----------------------------------------------------


def test_show_reflects_assigned_worktree(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    result = run_task_show(task_id, str(initialized_repo), write_log=False)
    assert result.data["task"]["worktree_id"] == name
    rendered = render_human(result)
    assert "assigned worktree: demo" in rendered
    assert "ownership: assigned" in rendered


def test_show_reflects_unassigned(initialized_repo):
    task_id = _create_task(initialized_repo)
    result = run_task_show(task_id, str(initialized_repo), write_log=False)
    assert result.data["task"]["worktree_id"] is None
    rendered = render_human(result)
    assert "ownership: unassigned" in rendered


def test_list_reflects_assigned_worktree(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    result = run_task_list(str(initialized_repo), write_log=False)
    assert result.data["tasks"][0]["worktree_id"] == name
    rendered = render_human(result)
    assert f"assigned_worktree={name}" in rendered


# --- regression: previous task/worktree commands unaffected -----------------------


def test_regression_task_create_still_works(initialized_repo):
    result = run_task_create("Another Task", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


def test_regression_task_close_still_works(initialized_repo, tmp_path):
    task_id = _create_task(initialized_repo)
    new_status = _fill_spec_and_close(initialized_repo, task_id, tmp_path)
    assert new_status == "completed"


def test_regression_worktree_list_still_works(initialized_repo):
    from forgeops.cli.worktree import run_worktree_list
    result = run_worktree_list(str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


def test_regression_worktree_remove_still_works(initialized_repo):
    from forgeops.cli.worktree import run_worktree_remove
    name = _create_worktree(initialized_repo)
    result = run_worktree_remove(name, str(initialized_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.SUCCESS


def test_trendforge_target_is_rejected(tmp_path, monkeypatch):
    fake_reference = tmp_path / "FakeTrendForge"
    fake_reference.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(fake_reference), check=True)
    monkeypatch.setattr("forgeops.core.paths.READONLY_REFERENCE_REPO", fake_reference)
    result = run_task_assign("task-0001", "demo", str(fake_reference), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED


def test_uninitialized_project_rejected(git_repo):
    result = run_task_assign("task-0001", "demo", str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
