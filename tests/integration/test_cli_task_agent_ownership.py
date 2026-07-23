"""Integration tests for `forgeops task assign-agent`/`forgeops task
unassign-agent` (forgeops/cli/task.py). See docs/tasks.md and
docs/agents.md "Ownership" for the one-to-one model, dry-run semantics,
and confirmation model this exercises end-to-end through the CLI entry
points."""
from __future__ import annotations

import json
import subprocess

from forgeops.cli.agent import run_agent_register
from forgeops.cli.task import (
    render_human,
    run_task_assign,
    run_task_assign_agent,
    run_task_close,
    run_task_create,
    run_task_list,
    run_task_show,
    run_task_unassign_agent,
    run_task_validate,
)
from forgeops.cli.worktree import run_worktree_create, run_worktree_remove
from forgeops.core import exit_codes
from forgeops.state.agent_registry import load_agent_registry
from forgeops.state.task_registry import (
    VALIDATION_STATUS_PASSED,
    load_task_record,
    load_validation_record,
    save_validation_record,
    task_dir_for,
)


def _create_task(repo, title="My Task"):
    result = run_task_create(title, str(repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    return result.data["task_id"]


def _register_agent(repo, agent_id="claude-primary", kind="claude"):
    result = run_agent_register(agent_id, kind, str(repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    return agent_id


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


# --- assignment ------------------------------------------------------------------


def test_assign_agent_success(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    result = run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT  # no worktree yet
    assert result.data["action"] == "agent_assigned"
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.agent_id == agent_id
    registry = load_agent_registry(initialized_repo)
    assert registry.records[0].assigned_task_id == task_id


def test_assign_agent_dry_run_zero_mutation(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    before = set(initialized_repo.rglob("*"))
    result = run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False, dry_run=True)
    after = set(initialized_repo.rglob("*"))
    assert result.data["action"] == "would_assign_agent"
    assert before == after


def test_assign_agent_human_json_agree(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    result = run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops task assign-agent" in rendered
    assert payload["data"]["agent_id"] == agent_id


def test_assign_agent_task_without_worktree_warns_but_succeeds(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    result = run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    assert any(w["key"] == "task-has-no-worktree" for w in result.data["warnings"])
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.agent_id == agent_id  # still succeeded


def test_assign_agent_task_with_worktree_succeeds_cleanly(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    agent_id = _register_agent(initialized_repo)
    result = run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["warnings"] == []


def test_assign_agent_task_already_assigned_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent1 = _register_agent(initialized_repo, "claude-primary")
    agent2 = _register_agent(initialized_repo, "codex-reviewer", "codex")
    run_task_assign_agent(task_id, agent1, str(initialized_repo), write_log=False)
    result = run_task_assign_agent(task_id, agent2, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-already-has-agent" for c in result.data["conflicts"])


def test_assign_agent_already_assigned_refused(initialized_repo):
    task_id1 = _create_task(initialized_repo, "Task One")
    task_id2 = _create_task(initialized_repo, "Task Two")
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id1, agent_id, str(initialized_repo), write_log=False)
    result = run_task_assign_agent(task_id2, agent_id, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "agent-already-assigned" for c in result.data["conflicts"])


def test_assign_agent_missing_task(initialized_repo):
    agent_id = _register_agent(initialized_repo)
    result = run_task_assign_agent("task-9999", agent_id, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-not-found" for c in result.data["conflicts"])


def test_assign_agent_missing_agent(initialized_repo):
    task_id = _create_task(initialized_repo)
    result = run_task_assign_agent(task_id, "no-such-agent", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "agent-not-found" for c in result.data["conflicts"])


def test_assign_agent_terminal_task_refused(initialized_repo, tmp_path):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    _fill_spec_and_close(initialized_repo, task_id, tmp_path)
    result = run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-already-terminal" for c in result.data["conflicts"])


def test_assign_agent_disabled_agent_refused(initialized_repo):
    from forgeops.state.agent_registry import STATUS_DISABLED, AgentRegistryDocument, load_agent_registry, save_agent_registry
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    registry = load_agent_registry(initialized_repo)
    tampered = type(registry.records[0])(**{**registry.records[0].__dict__, "status": STATUS_DISABLED})
    save_agent_registry(initialized_repo, AgentRegistryDocument(records=[tampered]))
    result = run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "agent-disabled" for c in result.data["conflicts"])


def test_assign_agent_malformed_task(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "TASK.json").write_text("{ not valid", encoding="utf-8")
    result = run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-json-malformed" for c in result.data["conflicts"])


def test_assign_agent_malformed_agent_registry(initialized_repo):
    task_id = _create_task(initialized_repo)
    registry_path = initialized_repo / ".agent" / "agents" / "AGENT_REGISTRY.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{ not valid", encoding="utf-8")
    result = run_task_assign_agent(task_id, "claude-primary", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "agent-registry-malformed" for c in result.data["conflicts"])


def test_assign_agent_index_mismatch(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    index_path = initialized_repo / ".agent" / "tasks" / "TASK_INDEX.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["records"][0]["agent_id"] = "some-other-agent"
    index_path.write_text(json.dumps(payload), encoding="utf-8")
    result = run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-index-mismatch" for c in result.data["conflicts"])


def test_assign_agent_reciprocal_mismatch_detected_via_toctou(initialized_repo):
    task_id1 = _create_task(initialized_repo, "Task One")
    task_id2 = _create_task(initialized_repo, "Task Two")
    agent_id = _register_agent(initialized_repo)
    from forgeops.state.task_ownership import apply_task_assign_agent, build_task_assign_agent_plan
    plan = build_task_assign_agent_plan(initialized_repo, task_id1, agent_id)
    assert not plan.has_conflict
    run_task_assign_agent(task_id2, agent_id, str(initialized_repo), write_log=False)
    outcome = apply_task_assign_agent(initialized_repo, plan)
    assert outcome.ok is False


def test_assign_agent_atomic_rollback(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    monkeypatch.setattr(
        "forgeops.state.task_ownership.save_agent_registry",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    result = run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.agent_id is None


def test_assign_agent_index_write_failure(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    monkeypatch.setattr(
        "forgeops.state.task_ownership.save_task_index",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    result = run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.agent_id == agent_id


def test_assign_agent_no_git_or_worktree_mutation(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    remotes_before = subprocess.run(["git", "remote", "-v"], cwd=str(initialized_repo), capture_output=True, text=True).stdout
    assert remotes_before == ""
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.worktree_id is None  # untouched


def test_assign_agent_spacey_paths(spacey_initialized_repo):
    task_id = _create_task(spacey_initialized_repo)
    agent_id = _register_agent(spacey_initialized_repo)
    result = run_task_assign_agent(task_id, agent_id, str(spacey_initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT


# --- show / list / validate reflect agent ownership --------------------------------


def test_show_reflects_assigned_agent(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    result = run_task_show(task_id, str(initialized_repo), write_log=False)
    assert result.data["task"]["agent_id"] == agent_id
    rendered = render_human(result)
    assert "assigned agent: claude-primary" in rendered
    assert "agent ownership: assigned" in rendered


def test_list_reflects_assigned_agent(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    result = run_task_list(str(initialized_repo), write_log=False)
    assert result.data["tasks"][0]["agent_id"] == agent_id
    rendered = render_human(result)
    assert f"assigned_agent={agent_id}" in rendered


def test_validate_reflects_no_agent_issues(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    result = run_task_validate(task_id, str(initialized_repo), write_log=False)
    agent_keys = {"agent-missing", "agent-reciprocal-mismatch", "duplicate-agent-assignment"}
    assert not any(b["key"] in agent_keys for b in result.data["blockers"])


# --- unassignment ------------------------------------------------------------------


def test_unassign_agent_confirmation_required(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    before = set(initialized_repo.rglob("*"))
    result = run_task_unassign_agent(task_id, str(initialized_repo), write_log=False)
    after = set(initialized_repo.rglob("*"))
    assert result.exit_code == exit_codes.BLOCKED
    assert result.data["action"] == "confirmation_required"
    assert before == after


def test_unassign_agent_dry_run_zero_mutation(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    before = set(initialized_repo.rglob("*"))
    result = run_task_unassign_agent(task_id, str(initialized_repo), write_log=False, dry_run=True)
    after = set(initialized_repo.rglob("*"))
    assert result.data["action"] == "would_unassign_agent"
    assert before == after


def test_unassign_agent_confirmed_success(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    result = run_task_unassign_agent(task_id, str(initialized_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.SUCCESS
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.agent_id is None
    registry = load_agent_registry(initialized_repo)
    assert registry.records[0].assigned_task_id is None


def test_double_unassign_agent_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    run_task_unassign_agent(task_id, str(initialized_repo), write_log=False, confirm=True)
    result = run_task_unassign_agent(task_id, str(initialized_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-not-assigned-agent" for c in result.data["conflicts"])


def test_unassign_agent_missing_reciprocal_ownership(initialized_repo):
    from forgeops.state.agent_registry import AgentRegistryDocument, save_agent_registry
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    save_agent_registry(initialized_repo, AgentRegistryDocument(records=[]))
    result = run_task_unassign_agent(task_id, str(initialized_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.SUCCESS
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.agent_id is None


def test_unassign_agent_rollback_on_registry_failure(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    monkeypatch.setattr(
        "forgeops.state.task_ownership.save_agent_registry",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    result = run_task_unassign_agent(task_id, str(initialized_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.agent_id == agent_id


def test_unassign_agent_no_agent_deletion(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    run_task_unassign_agent(task_id, str(initialized_repo), write_log=False, confirm=True)
    registry = load_agent_registry(initialized_repo)
    assert len(registry.records) == 1


def test_unassign_agent_no_task_status_change(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    run_task_unassign_agent(task_id, str(initialized_repo), write_log=False, confirm=True)
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.status == "draft"


def test_unassign_agent_no_worktree_or_git_mutation(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    run_task_unassign_agent(task_id, str(initialized_repo), write_log=False, confirm=True)
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.worktree_id == name


def test_unassign_agent_human_json_agree(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    result = run_task_unassign_agent(task_id, str(initialized_repo), write_log=False, confirm=True)
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops task unassign-agent" in rendered
    assert payload["data"]["current_agent"] == agent_id


# --- regression -----------------------------------------------------------------


def test_regression_worktree_ownership_unaffected(initialized_repo):
    task_id = _create_task(initialized_repo)
    name = _create_worktree(initialized_repo)
    result = run_task_assign(task_id, name, str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


def test_regression_worktree_remove_still_works(initialized_repo):
    name = _create_worktree(initialized_repo)
    result = run_worktree_remove(name, str(initialized_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.SUCCESS


def test_regression_task_close_still_works(initialized_repo, tmp_path):
    task_id = _create_task(initialized_repo)
    _fill_spec_and_close(initialized_repo, task_id, tmp_path)
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.status == "completed"
