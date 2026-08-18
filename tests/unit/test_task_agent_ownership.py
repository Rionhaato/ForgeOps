"""Tests for the agent-assignment portion of forgeops/state/task_ownership.py:
`build_task_assign_agent_plan`/`apply_task_assign_agent` and
`build_task_unassign_agent_plan`/`apply_task_unassign_agent`."""
from __future__ import annotations

from forgeops.state.agent_register import apply_agent_register, build_agent_register_plan
from forgeops.state.agent_registry import STATUS_DISABLED, load_agent_registry
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
    apply_task_assign_agent,
    apply_task_unassign_agent,
    build_task_assign_agent_plan,
    build_task_assign_plan,
    build_task_unassign_agent_plan,
)
from forgeops.state.task_registry import (
    VALIDATION_STATUS_PASSED,
    ApprovalEvent,
    load_task_record,
    load_validation_record,
    save_task_record,
    save_validation_record,
    task_dir_for,
)
from forgeops.state.worktree_create import apply_worktree_create, build_worktree_create_plan

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


def _register_agent(repo_root, agent_id="claude-primary", kind="claude"):
    plan = build_agent_register_plan(repo_root, agent_id, kind, None)
    outcome = apply_agent_register(repo_root, plan)
    assert outcome.ok is True
    return agent_id


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


# --- assign-agent: preflight --------------------------------------------------


def test_assign_agent_clean_plan_has_no_conflicts(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    plan = build_task_assign_agent_plan(initialized_repo, task_id, agent_id)
    assert plan.has_conflict is False


def test_assign_agent_plan_never_writes_anything(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    before = set(initialized_repo.rglob("*"))
    build_task_assign_agent_plan(initialized_repo, task_id, agent_id)
    after = set(initialized_repo.rglob("*"))
    assert before == after


def test_assign_agent_task_without_worktree_warns_but_eligible(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    plan = build_task_assign_agent_plan(initialized_repo, task_id, agent_id)
    assert plan.has_conflict is False
    assert any(w.key == "task-has-no-worktree" for w in plan.warnings)


def test_assign_agent_task_with_worktree_no_warning(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    apply_task_assign(initialized_repo, build_task_assign_plan(initialized_repo, task_id, name))
    agent_id = _register_agent(initialized_repo)
    plan = build_task_assign_agent_plan(initialized_repo, task_id, agent_id)
    assert plan.has_conflict is False
    assert not any(w.key == "task-has-no-worktree" for w in plan.warnings)


def test_assign_agent_missing_task(initialized_repo):
    agent_id = _register_agent(initialized_repo)
    plan = build_task_assign_agent_plan(initialized_repo, "task-9999", agent_id)
    assert any(c.key == "task-not-found" for c in plan.conflicts)


def test_assign_agent_missing_agent(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_assign_agent_plan(initialized_repo, task_id, "no-such-agent")
    assert any(c.key == "agent-not-found" for c in plan.conflicts)


def test_assign_agent_disabled_agent_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    registry = load_agent_registry(initialized_repo)
    from forgeops.state.agent_registry import AgentRegistryDocument, save_agent_registry
    tampered = type(registry.records[0])(**{**registry.records[0].__dict__, "status": STATUS_DISABLED})
    save_agent_registry(initialized_repo, AgentRegistryDocument(records=[tampered]))
    plan = build_task_assign_agent_plan(initialized_repo, task_id, agent_id)
    assert any(c.key == "agent-disabled" for c in plan.conflicts)


def test_assign_agent_terminal_task_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    _close_task(initialized_repo, task_id)
    plan = build_task_assign_agent_plan(initialized_repo, task_id, agent_id)
    assert any(c.key == "task-already-terminal" for c in plan.conflicts)


def test_assign_agent_task_already_has_agent(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent1 = _register_agent(initialized_repo, "claude-primary")
    agent2 = _register_agent(initialized_repo, "codex-reviewer", "codex")
    apply_task_assign_agent(initialized_repo, build_task_assign_agent_plan(initialized_repo, task_id, agent1))
    plan = build_task_assign_agent_plan(initialized_repo, task_id, agent2)
    assert any(c.key == "task-already-has-agent" for c in plan.conflicts)


def test_assign_agent_already_assigned_elsewhere(initialized_repo):
    task_id1 = _create_task(initialized_repo, "Task One")
    task_id2 = _create_task(initialized_repo, "Task Two")
    agent_id = _register_agent(initialized_repo)
    apply_task_assign_agent(initialized_repo, build_task_assign_agent_plan(initialized_repo, task_id1, agent_id))
    plan = build_task_assign_agent_plan(initialized_repo, task_id2, agent_id)
    assert any(c.key == "agent-already-assigned" for c in plan.conflicts)


def test_assign_agent_malformed_task(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "TASK.json").write_text("{ not valid", encoding="utf-8")
    plan = build_task_assign_agent_plan(initialized_repo, task_id, agent_id)
    assert any(c.key == "task-json-malformed" for c in plan.conflicts)


def test_assign_agent_malformed_agent_registry(initialized_repo):
    task_id = _create_task(initialized_repo)
    registry_path = initialized_repo / ".agent" / "agents" / "AGENT_REGISTRY.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("{ not valid", encoding="utf-8")
    plan = build_task_assign_agent_plan(initialized_repo, task_id, "claude-primary")
    assert any(c.key == "agent-registry-malformed" for c in plan.conflicts)


def test_assign_agent_index_mismatch(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    import json
    index_path = initialized_repo / ".agent" / "tasks" / "TASK_INDEX.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["records"][0]["agent_id"] = "some-other-agent"
    index_path.write_text(json.dumps(payload), encoding="utf-8")
    plan = build_task_assign_agent_plan(initialized_repo, task_id, agent_id)
    assert any(c.key == "task-index-mismatch" for c in plan.conflicts)


# --- assign-agent: apply / success ----------------------------------------------


def test_assign_agent_success_updates_all_three_files(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    plan = build_task_assign_agent_plan(initialized_repo, task_id, agent_id)
    outcome = apply_task_assign_agent(initialized_repo, plan)
    assert outcome.ok is True
    assert outcome.task_json_written is True
    assert outcome.agent_registry_written is True
    assert outcome.index_written is True

    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.agent_id == agent_id
    registry = load_agent_registry(initialized_repo)
    assert registry.records[0].assigned_task_id == task_id
    assert registry.records[0].updated_at is not None

    from forgeops.state.task_registry import load_index
    index = load_index(initialized_repo)
    idx_record = next(r for r in index.records if r.task_id == task_id)
    assert idx_record.agent_id == agent_id


def test_assign_agent_index_write_failure_still_reports_ok(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    plan = build_task_assign_agent_plan(initialized_repo, task_id, agent_id)
    monkeypatch.setattr(
        "forgeops.state.task_ownership.save_task_index",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_assign_agent(initialized_repo, plan)
    assert outcome.ok is True
    assert outcome.index_written is False
    assert outcome.index_error is not None
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.agent_id == agent_id


def test_assign_agent_atomic_rollback_on_registry_failure(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    plan = build_task_assign_agent_plan(initialized_repo, task_id, agent_id)
    monkeypatch.setattr(
        "forgeops.state.task_ownership.save_agent_registry",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_assign_agent(initialized_repo, plan)
    assert outcome.ok is False
    assert outcome.partial_state is not None
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.agent_id is None
    registry = load_agent_registry(initialized_repo)
    assert registry.records[0].assigned_task_id is None


def test_assign_agent_partial_write_failure_on_task_json(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    plan = build_task_assign_agent_plan(initialized_repo, task_id, agent_id)
    monkeypatch.setattr(
        "forgeops.state.task_ownership.save_task_record",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_assign_agent(initialized_repo, plan)
    assert outcome.ok is False
    registry = load_agent_registry(initialized_repo)
    assert registry.records[0].assigned_task_id is None


def test_assign_agent_toctou_state_changed(initialized_repo):
    task_id1 = _create_task(initialized_repo, "Task One")
    task_id2 = _create_task(initialized_repo, "Task Two")
    agent_id = _register_agent(initialized_repo)
    plan = build_task_assign_agent_plan(initialized_repo, task_id1, agent_id)
    assert not plan.has_conflict
    other_plan = build_task_assign_agent_plan(initialized_repo, task_id2, agent_id)
    apply_task_assign_agent(initialized_repo, other_plan)
    outcome = apply_task_assign_agent(initialized_repo, plan)
    assert outcome.ok is False
    task_record = load_task_record(task_dir_for(initialized_repo, task_id1)).record
    assert task_record.agent_id is None


def test_assign_agent_no_worktree_mutation(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    plan = build_task_assign_agent_plan(initialized_repo, task_id, agent_id)
    apply_task_assign_agent(initialized_repo, plan)
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.worktree_id is None  # never assigned, never touched


# --- unassign-agent: preflight -----------------------------------------------


def test_unassign_agent_clean_plan_has_no_conflicts(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    apply_task_assign_agent(initialized_repo, build_task_assign_agent_plan(initialized_repo, task_id, agent_id))
    plan = build_task_unassign_agent_plan(initialized_repo, task_id)
    assert plan.has_conflict is False


def test_unassign_agent_plan_never_writes_anything(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    apply_task_assign_agent(initialized_repo, build_task_assign_agent_plan(initialized_repo, task_id, agent_id))
    before = set(initialized_repo.rglob("*"))
    build_task_unassign_agent_plan(initialized_repo, task_id)
    after = set(initialized_repo.rglob("*"))
    assert before == after


def test_unassign_agent_not_assigned_is_a_conflict(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_unassign_agent_plan(initialized_repo, task_id)
    assert any(c.key == "task-not-assigned-agent" for c in plan.conflicts)


def test_unassign_agent_missing_task(initialized_repo):
    plan = build_task_unassign_agent_plan(initialized_repo, "task-9999")
    assert any(c.key == "task-not-found" for c in plan.conflicts)


# --- unassign-agent: apply ----------------------------------------------------


def test_unassign_agent_success_clears_both_records(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    apply_task_assign_agent(initialized_repo, build_task_assign_agent_plan(initialized_repo, task_id, agent_id))
    plan = build_task_unassign_agent_plan(initialized_repo, task_id)
    outcome = apply_task_unassign_agent(initialized_repo, plan)
    assert outcome.ok is True
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.agent_id is None
    registry = load_agent_registry(initialized_repo)
    assert registry.records[0].assigned_task_id is None


def test_unassign_agent_no_agent_deletion(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    apply_task_assign_agent(initialized_repo, build_task_assign_agent_plan(initialized_repo, task_id, agent_id))
    apply_task_unassign_agent(initialized_repo, build_task_unassign_agent_plan(initialized_repo, task_id))
    registry = load_agent_registry(initialized_repo)
    assert len(registry.records) == 1  # agent record still present
    assert registry.records[0].agent_id == agent_id


def test_unassign_agent_no_task_status_change(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    apply_task_assign_agent(initialized_repo, build_task_assign_agent_plan(initialized_repo, task_id, agent_id))
    apply_task_unassign_agent(initialized_repo, build_task_unassign_agent_plan(initialized_repo, task_id))
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.status == "draft"


def test_double_unassign_agent_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    apply_task_assign_agent(initialized_repo, build_task_assign_agent_plan(initialized_repo, task_id, agent_id))
    plan1 = build_task_unassign_agent_plan(initialized_repo, task_id)
    apply_task_unassign_agent(initialized_repo, plan1)
    plan2 = build_task_unassign_agent_plan(initialized_repo, task_id)
    assert any(c.key == "task-not-assigned-agent" for c in plan2.conflicts)


def test_unassign_agent_missing_reciprocal_registry_record(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    apply_task_assign_agent(initialized_repo, build_task_assign_agent_plan(initialized_repo, task_id, agent_id))
    # Simulate the agent record having been removed from the registry independently.
    from forgeops.state.agent_registry import AgentRegistryDocument, save_agent_registry
    save_agent_registry(initialized_repo, AgentRegistryDocument(records=[]))

    plan = build_task_unassign_agent_plan(initialized_repo, task_id)
    outcome = apply_task_unassign_agent(initialized_repo, plan)
    assert outcome.ok is True
    assert outcome.agent_registry_written is False
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.agent_id is None


def test_unassign_agent_atomic_rollback_on_registry_failure(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    apply_task_assign_agent(initialized_repo, build_task_assign_agent_plan(initialized_repo, task_id, agent_id))
    plan = build_task_unassign_agent_plan(initialized_repo, task_id)
    monkeypatch.setattr(
        "forgeops.state.task_ownership.save_agent_registry",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_unassign_agent(initialized_repo, plan)
    assert outcome.ok is False
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.agent_id == agent_id  # rolled back, still assigned


def test_unassign_agent_no_worktree_or_git_mutation(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    apply_task_assign(initialized_repo, build_task_assign_plan(initialized_repo, task_id, name))
    agent_id = _register_agent(initialized_repo)
    apply_task_assign_agent(initialized_repo, build_task_assign_agent_plan(initialized_repo, task_id, agent_id))
    apply_task_unassign_agent(initialized_repo, build_task_unassign_agent_plan(initialized_repo, task_id))
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.worktree_id == name  # untouched by agent unassignment


# --- regression: approval_history must survive agent ownership mutations ------
#
# See test_task_ownership.py's identical regression tests for the full
# explanation: apply_task_assign_agent/apply_task_unassign_agent used to
# reconstruct TaskRecord via `TaskRecord(**{**original.to_dict(), ...})`,
# which silently stored plain dicts in approval_history instead of
# ApprovalEvent objects whenever that field wasn't explicitly overridden.


def test_assign_agent_preserves_approval_history(initialized_repo):
    task_id = _create_task(initialized_repo)
    _approve_task(initialized_repo, task_id)
    agent_id = _register_agent(initialized_repo)
    plan = build_task_assign_agent_plan(initialized_repo, task_id, agent_id)
    outcome = apply_task_assign_agent(initialized_repo, plan)
    assert outcome.ok is True

    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert [type(e) for e in task_record.approval_history] == [ApprovalEvent, ApprovalEvent]
    assert [e.action for e in task_record.approval_history] == ["requested", "approved"]
    # Proves the record round-trips: to_dict() would raise AttributeError
    # if approval_history still held plain dicts instead of ApprovalEvent objects.
    assert task_record.to_dict()["approval_history"] == [e.to_dict() for e in task_record.approval_history]

    reloaded = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert [e.action for e in reloaded.approval_history] == ["requested", "approved"]
    assert reloaded.agent_id == agent_id


def test_unassign_agent_preserves_approval_history(initialized_repo):
    task_id = _create_task(initialized_repo)
    _approve_task(initialized_repo, task_id)
    agent_id = _register_agent(initialized_repo)
    apply_task_assign_agent(initialized_repo, build_task_assign_agent_plan(initialized_repo, task_id, agent_id))

    plan = build_task_unassign_agent_plan(initialized_repo, task_id)
    outcome = apply_task_unassign_agent(initialized_repo, plan)
    assert outcome.ok is True

    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert [type(e) for e in task_record.approval_history] == [ApprovalEvent, ApprovalEvent]
    assert [e.action for e in task_record.approval_history] == ["requested", "approved"]
    assert task_record.to_dict()["approval_history"] == [e.to_dict() for e in task_record.approval_history]

    reloaded = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert [e.action for e in reloaded.approval_history] == ["requested", "approved"]
    assert reloaded.agent_id is None
