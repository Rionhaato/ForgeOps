"""Tests for forgeops/state/task_ownership.py: the read-only preflight
plan builders and mutating apply steps behind `forgeops task
assign`/`forgeops task unassign`."""
from __future__ import annotations

from forgeops.state.task_approval import (
    apply_task_approve,
    apply_task_request_approval,
    build_task_approve_plan,
    build_task_request_approval_plan,
)
from forgeops.state.task_close import apply_task_close, build_task_close_plan
from forgeops.state.task_create import apply_task_create, build_task_create_plan
from forgeops.state.task_ownership import (
    apply_task_assign,
    apply_task_unassign,
    build_task_assign_plan,
    build_task_unassign_plan,
)
from forgeops.state.task_registry import (
    VALIDATION_STATUS_PASSED,
    ApprovalEvent,
    load_index as load_task_index,
    load_task_record,
    load_validation_record,
    save_validation_record,
    task_dir_for,
)
from forgeops.state.worktree_create import apply_worktree_create, build_worktree_create_plan
from forgeops.state.worktree_registry import STATUS_REMOVED, load_registry as load_worktree_registry, save_registry
from forgeops.worktrees.naming import worktree_path_for

MAX_BYTES = 2 * 1024 * 1024


def _create_task(repo_root, title="My Task"):
    plan = build_task_create_plan(repo_root, title, None, None, MAX_BYTES)
    outcome = apply_task_create(repo_root, plan)
    assert outcome.ok is True
    return plan.task_id


def _create_worktree(repo_root, name="demo"):
    plan = build_worktree_create_plan(repo_root, name, None, None)
    outcome = apply_worktree_create(repo_root, plan)
    assert outcome.ok is True
    return name


def _approve_task(repo_root, task_id, actor="joshua"):
    apply_task_request_approval(repo_root, build_task_request_approval_plan(repo_root, task_id, actor))
    apply_task_approve(repo_root, build_task_approve_plan(repo_root, task_id, actor))


def _close_task(repo_root, task_id, status=VALIDATION_STATUS_PASSED):
    task_dir = task_dir_for(repo_root, task_id)
    (task_dir / "SPEC.md").write_text(
        "# SPEC\n\n## Objective\n\nfoo\n\n## In Scope\n\nfoo\n\n## Out of Scope\n\nfoo\n\n"
        "## Constraints\n\nfoo\n\n## Acceptance Criteria\n\n- works\n\n"
        "## Required Validation\n\n- pytest\n\n## Stop Boundary\n\nfoo\n",
        encoding="utf-8",
    )
    load = load_validation_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "status": status})
    save_validation_record(task_dir, tampered)
    import tempfile
    from pathlib import Path
    result_file = Path(tempfile.mkstemp(suffix=".md")[1])
    result_file.write_text("## Outcome\n\ndone\n", encoding="utf-8")

    def _resolve_ok(repo_root, ref):
        return "deadbeef" if ref else None

    plan = build_task_close_plan(repo_root, task_id, result_file, MAX_BYTES, True, _resolve_ok)
    outcome = apply_task_close(repo_root, plan)
    assert outcome.ok is True
    return outcome.new_status


# --- assignment: preflight --------------------------------------------------


def test_assign_clean_plan_has_no_conflicts(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    plan = build_task_assign_plan(initialized_repo, task_id, name)
    assert plan.has_conflict is False


def test_assign_plan_never_writes_anything(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    before = set(initialized_repo.rglob("*"))
    build_task_assign_plan(initialized_repo, task_id, name)
    after = set(initialized_repo.rglob("*"))
    assert before == after


def test_assign_missing_task_is_a_conflict(initialized_repo):
    name = _create_worktree(initialized_repo)
    plan = build_task_assign_plan(initialized_repo, "task-9999", name)
    assert any(c.key == "task-not-found" for c in plan.conflicts)


def test_assign_missing_worktree_is_a_conflict(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_assign_plan(initialized_repo, task_id, "no-such-worktree")
    assert any(c.key == "worktree-not-found" for c in plan.conflicts)


def test_assign_removed_worktree_is_a_conflict(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    registry = load_worktree_registry(initialized_repo)
    tampered = type(registry.records[0])(**{**registry.records[0].__dict__, "status": STATUS_REMOVED})
    from forgeops.state.worktree_registry import WorktreeRegistryDocument
    save_registry(initialized_repo, WorktreeRegistryDocument(records=[tampered]))
    plan = build_task_assign_plan(initialized_repo, task_id, name)
    assert any(c.key == "worktree-removed" for c in plan.conflicts)


def test_assign_completed_task_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    _close_task(initialized_repo, task_id, status=VALIDATION_STATUS_PASSED)
    plan = build_task_assign_plan(initialized_repo, task_id, name)
    assert any(c.key == "task-already-terminal" for c in plan.conflicts)


def test_assign_failed_task_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    from forgeops.state.task_registry import VALIDATION_STATUS_FAILED
    _close_task(initialized_repo, task_id, status=VALIDATION_STATUS_FAILED)
    plan = build_task_assign_plan(initialized_repo, task_id, name)
    assert any(c.key == "task-already-terminal" for c in plan.conflicts)


def test_assign_task_currently_assigned_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    name1 = _create_worktree(initialized_repo, "demo1")
    name2 = _create_worktree(initialized_repo, "demo2")
    plan1 = build_task_assign_plan(initialized_repo, task_id, name1)
    apply_task_assign(initialized_repo, plan1)
    plan2 = build_task_assign_plan(initialized_repo, task_id, name2)
    assert any(c.key == "task-already-assigned" for c in plan2.conflicts)


def test_assign_worktree_currently_assigned_refused(initialized_repo):
    task_id1 = _create_task(initialized_repo, "Task One")
    task_id2 = _create_task(initialized_repo, "Task Two")
    name = _create_worktree(initialized_repo)
    plan1 = build_task_assign_plan(initialized_repo, task_id1, name)
    apply_task_assign(initialized_repo, plan1)
    plan2 = build_task_assign_plan(initialized_repo, task_id2, name)
    assert any(c.key == "worktree-already-assigned" for c in plan2.conflicts)


def test_assign_registry_corruption_is_a_conflict(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    registry_path = initialized_repo / ".agent" / "runtime" / "WORKTREE_REGISTRY.json"
    registry_path.write_text("{ not valid", encoding="utf-8")
    plan = build_task_assign_plan(initialized_repo, task_id, name)
    assert any(c.key == "worktree-registry-malformed" for c in plan.conflicts)


def test_assign_task_schema_mismatch_is_a_conflict(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "TASK.json").write_text("{ not valid", encoding="utf-8")
    plan = build_task_assign_plan(initialized_repo, task_id, name)
    assert any(c.key == "task-json-malformed" for c in plan.conflicts)


def test_assign_locked_worktree_refused(initialized_repo):
    import subprocess
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    wt_path = worktree_path_for(initialized_repo, name)
    subprocess.run(["git", "worktree", "lock", "--reason", "test lock", str(wt_path)], cwd=str(initialized_repo), check=True, capture_output=True, text=True)
    plan = build_task_assign_plan(initialized_repo, task_id, name)
    assert any(c.key == "worktree-locked" for c in plan.conflicts)


def test_assign_windows_path_with_spaces(spacey_initialized_repo):
    task_id = _create_task(spacey_initialized_repo)
    name = _create_worktree(spacey_initialized_repo)
    plan = build_task_assign_plan(spacey_initialized_repo, task_id, name)
    assert plan.has_conflict is False
    outcome = apply_task_assign(spacey_initialized_repo, plan)
    assert outcome.ok is True


# --- assignment: apply / success ----------------------------------------------


def test_assign_success_updates_all_three_files(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    plan = build_task_assign_plan(initialized_repo, task_id, name)
    outcome = apply_task_assign(initialized_repo, plan)
    assert outcome.ok is True
    assert outcome.task_json_written is True
    assert outcome.worktree_registry_written is True
    assert outcome.index_written is True

    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.worktree_id == name
    registry = load_worktree_registry(initialized_repo)
    assert registry.records[0].task_id == task_id
    assert registry.records[0].updated_at is not None
    index = load_task_index(initialized_repo)
    idx_record = next(r for r in index.records if r.task_id == task_id)
    assert idx_record.worktree_id == name


def test_assign_index_write_failure_still_reports_ok(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    plan = build_task_assign_plan(initialized_repo, task_id, name)
    monkeypatch.setattr(
        "forgeops.state.task_ownership.save_task_index",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_assign(initialized_repo, plan)
    assert outcome.ok is True
    assert outcome.index_written is False
    assert outcome.index_error is not None
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.worktree_id == name


def test_assign_atomic_rollback_on_worktree_registry_failure(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    plan = build_task_assign_plan(initialized_repo, task_id, name)
    monkeypatch.setattr(
        "forgeops.state.task_ownership.save_worktree_registry",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_assign(initialized_repo, plan)
    assert outcome.ok is False
    assert outcome.partial_state is not None
    # Rolled back - TASK.json must not claim an assignment the registry doesn't have.
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.worktree_id is None
    registry = load_worktree_registry(initialized_repo)
    assert registry.records[0].task_id is None


def test_assign_partial_write_failure_on_task_json(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    plan = build_task_assign_plan(initialized_repo, task_id, name)
    monkeypatch.setattr(
        "forgeops.state.task_ownership.save_task_record",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_assign(initialized_repo, plan)
    assert outcome.ok is False
    assert outcome.partial_state is not None
    registry = load_worktree_registry(initialized_repo)
    assert registry.records[0].task_id is None  # never touched


def test_assign_toctou_state_changed_between_preflight_and_apply(initialized_repo):
    task_id1 = _create_task(initialized_repo, "Task One")
    task_id2 = _create_task(initialized_repo, "Task Two")
    name = _create_worktree(initialized_repo)
    plan = build_task_assign_plan(initialized_repo, task_id1, name)
    assert not plan.has_conflict
    # Someone else assigns the worktree in the meantime.
    other_plan = build_task_assign_plan(initialized_repo, task_id2, name)
    apply_task_assign(initialized_repo, other_plan)
    # The original (stale) plan must now be refused, not silently applied.
    outcome = apply_task_assign(initialized_repo, plan)
    assert outcome.ok is False
    task_record = load_task_record(task_dir_for(initialized_repo, task_id1)).record
    assert task_record.worktree_id is None


# --- unassign: preflight -----------------------------------------------------


def test_unassign_clean_plan_has_no_conflicts(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    apply_task_assign(initialized_repo, build_task_assign_plan(initialized_repo, task_id, name))
    plan = build_task_unassign_plan(initialized_repo, task_id)
    assert plan.has_conflict is False


def test_unassign_plan_never_writes_anything(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    apply_task_assign(initialized_repo, build_task_assign_plan(initialized_repo, task_id, name))
    before = set(initialized_repo.rglob("*"))
    build_task_unassign_plan(initialized_repo, task_id)
    after = set(initialized_repo.rglob("*"))
    assert before == after


def test_unassign_not_assigned_is_a_conflict(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_unassign_plan(initialized_repo, task_id)
    assert any(c.key == "task-not-assigned" for c in plan.conflicts)


def test_unassign_missing_task_is_a_conflict(initialized_repo):
    plan = build_task_unassign_plan(initialized_repo, "task-9999")
    assert any(c.key == "task-not-found" for c in plan.conflicts)


# --- unassign: apply ----------------------------------------------------------


def test_unassign_success_clears_both_records(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    apply_task_assign(initialized_repo, build_task_assign_plan(initialized_repo, task_id, name))
    plan = build_task_unassign_plan(initialized_repo, task_id)
    outcome = apply_task_unassign(initialized_repo, plan)
    assert outcome.ok is True
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.worktree_id is None
    registry = load_worktree_registry(initialized_repo)
    assert registry.records[0].task_id is None


def test_unassign_after_worktree_independently_removed(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    apply_task_assign(initialized_repo, build_task_assign_plan(initialized_repo, task_id, name))
    # Simulate `worktree remove` (which never clears ownership itself).
    registry = load_worktree_registry(initialized_repo)
    tampered = type(registry.records[0])(**{**registry.records[0].__dict__, "status": STATUS_REMOVED})
    from forgeops.state.worktree_registry import WorktreeRegistryDocument
    save_registry(initialized_repo, WorktreeRegistryDocument(records=[tampered]))

    plan = build_task_unassign_plan(initialized_repo, task_id)
    outcome = apply_task_unassign(initialized_repo, plan)
    assert outcome.ok is True
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.worktree_id is None


def test_double_unassign_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    apply_task_assign(initialized_repo, build_task_assign_plan(initialized_repo, task_id, name))
    plan1 = build_task_unassign_plan(initialized_repo, task_id)
    apply_task_unassign(initialized_repo, plan1)
    plan2 = build_task_unassign_plan(initialized_repo, task_id)
    assert any(c.key == "task-not-assigned" for c in plan2.conflicts)


def test_unassign_atomic_rollback_on_registry_failure(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    apply_task_assign(initialized_repo, build_task_assign_plan(initialized_repo, task_id, name))
    plan = build_task_unassign_plan(initialized_repo, task_id)
    monkeypatch.setattr(
        "forgeops.state.task_ownership.save_worktree_registry",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_unassign(initialized_repo, plan)
    assert outcome.ok is False
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.worktree_id == name  # rolled back, still assigned


# --- regression: approval_history must survive ownership mutations ------------
#
# apply_task_assign/apply_task_unassign used to reconstruct TaskRecord via
# `TaskRecord(**{**original.to_dict(), ...})`. to_dict() serializes
# approval_history to plain dicts, so any field the call didn't explicitly
# override - including approval_history - got stored as those plain dicts
# instead of ApprovalEvent objects. That corrupted record then raised
# AttributeError the next time anything called .to_dict() on it (e.g. the
# very next save_task_record), but only once approval_history was non-empty -
# which is why this went unnoticed until a task was approved before being
# assigned a worktree.


def test_assign_preserves_approval_history(initialized_repo):
    task_id = _create_task(initialized_repo)
    _approve_task(initialized_repo, task_id)
    name = _create_worktree(initialized_repo)
    plan = build_task_assign_plan(initialized_repo, task_id, name)
    outcome = apply_task_assign(initialized_repo, plan)
    assert outcome.ok is True

    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert [type(e) for e in task_record.approval_history] == [ApprovalEvent, ApprovalEvent]
    assert [e.action for e in task_record.approval_history] == ["requested", "approved"]
    # Proves the record round-trips: to_dict() would raise AttributeError
    # if approval_history still held plain dicts instead of ApprovalEvent objects.
    assert task_record.to_dict()["approval_history"] == [e.to_dict() for e in task_record.approval_history]

    reloaded = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert [e.action for e in reloaded.approval_history] == ["requested", "approved"]
    assert reloaded.worktree_id == name


def test_unassign_preserves_approval_history(initialized_repo):
    task_id = _create_task(initialized_repo)
    _approve_task(initialized_repo, task_id)
    name = _create_worktree(initialized_repo)
    apply_task_assign(initialized_repo, build_task_assign_plan(initialized_repo, task_id, name))

    plan = build_task_unassign_plan(initialized_repo, task_id)
    outcome = apply_task_unassign(initialized_repo, plan)
    assert outcome.ok is True

    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert [type(e) for e in task_record.approval_history] == [ApprovalEvent, ApprovalEvent]
    assert [e.action for e in task_record.approval_history] == ["requested", "approved"]
    assert task_record.to_dict()["approval_history"] == [e.to_dict() for e in task_record.approval_history]

    reloaded = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert [e.action for e in reloaded.approval_history] == ["requested", "approved"]
    assert reloaded.worktree_id is None
