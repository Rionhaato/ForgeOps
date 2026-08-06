"""Tests for forgeops/state/task_validate.py: read-only structural/
consistency validation for a single task."""
from __future__ import annotations

import json

from forgeops.state.task_create import apply_task_create, build_task_create_plan
from forgeops.state.task_registry import (
    load_index,
    load_task_record,
    load_validation_record,
    save_index,
    save_task_record,
    save_validation_record,
    task_dir_for,
)
from forgeops.state.task_validate import validate_task

MAX_BYTES = 2 * 1024 * 1024


def _resolve_ok(repo_root, ref):
    return "deadbeef" if ref else None


def _resolve_none(repo_root, ref):
    return None


def _create(repo_root, title="My Task", spec_file=None, acceptance_file=None):
    plan = build_task_create_plan(repo_root, title, spec_file, acceptance_file, MAX_BYTES)
    outcome = apply_task_create(repo_root, plan)
    assert outcome.ok is True
    return plan.task_id


def _full_spec(repo_root, task_id):
    """Fill in every required SPEC.md section with real content, so
    validate_task reports zero blockers for an otherwise-untouched task."""
    task_dir = task_dir_for(repo_root, task_id)
    (task_dir / "SPEC.md").write_text(
        "# SPEC\n\n"
        "## Objective\n\nDo the thing.\n\n"
        "## In Scope\n\nThe thing.\n\n"
        "## Out of Scope\n\nEverything else.\n\n"
        "## Constraints\n\nSee CLAUDE.md.\n\n"
        "## Acceptance Criteria\n\n- it works\n\n"
        "## Required Validation\n\n- pytest\n\n"
        "## Stop Boundary\n\nDo not do more.\n",
        encoding="utf-8",
    )


# --- valid task -----------------------------------------------------------


def test_valid_draft_task_has_only_expected_blockers(initialized_repo):
    task_id = _create(initialized_repo)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    # A freshly created draft task has empty Acceptance Criteria/Required
    # Validation placeholders - those are the only expected blockers.
    blocker_keys = {i.key for i in outcome.blockers}
    assert blocker_keys == {"acceptance-criteria-missing", "required-validation-missing"}


def test_fully_specified_task_has_no_blockers(initialized_repo):
    task_id = _create(initialized_repo)
    _full_spec(initialized_repo, task_id)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert outcome.has_blockers is False


def test_validate_never_mutates(initialized_repo):
    task_id = _create(initialized_repo)
    _full_spec(initialized_repo, task_id)
    before = set(initialized_repo.rglob("*"))
    validate_task(initialized_repo, task_id, True, _resolve_ok)
    after = set(initialized_repo.rglob("*"))
    assert before == after


# --- structural issues ------------------------------------------------------


def test_missing_required_heading_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "SPEC.md").write_text("# SPEC\n\n## Objective\n\nfoo\n", encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "spec-missing-headings" for i in outcome.blockers)


def test_invalid_task_id_is_a_blocker(initialized_repo):
    outcome = validate_task(initialized_repo, "not-a-task-id", True, _resolve_ok)
    assert outcome.found is False
    assert any(i.key == "invalid-task-id" for i in outcome.blockers)


def test_unknown_task_id_reports_not_found(initialized_repo):
    outcome = validate_task(initialized_repo, "task-9999", True, _resolve_ok)
    assert outcome.found is False
    assert any(i.key == "not-found" for i in outcome.blockers)


def test_index_directory_mismatch_directory_missing(initialized_repo):
    task_id = _create(initialized_repo)
    import shutil
    shutil.rmtree(task_dir_for(initialized_repo, task_id))
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "index-directory-mismatch" for i in outcome.blockers)


def test_unindexed_task_directory_is_a_warning(initialized_repo):
    task_id = _create(initialized_repo)
    index = load_index(initialized_repo)
    save_index(initialized_repo, type(index)(schema_version=index.schema_version, next_task_number=index.next_task_number, records=[]))
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "index-directory-mismatch" and i.severity == "warning" for i in outcome.issues)


def test_project_root_mismatch_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    load = load_task_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "project_root": "C:\\some\\other\\repo"})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "project-root-mismatch" for i in outcome.blockers)


def test_invalid_status_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    load = load_task_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "status": "bogus_status"})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "invalid-status" for i in outcome.blockers)


def test_invalid_approval_state_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    load = load_task_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "approval_state": "bogus"})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "invalid-approval-state" for i in outcome.blockers)


def test_duplicate_task_id_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    index = load_index(initialized_repo)
    from forgeops.state.task_registry import TaskIndexDocument
    save_index(initialized_repo, TaskIndexDocument(
        schema_version=index.schema_version, next_task_number=index.next_task_number,
        records=[*index.records, index.records[0]],
    ))
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "duplicate-task-id" for i in outcome.blockers)


def test_malformed_validation_json_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "VALIDATION.json").write_text("{ not valid", encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "validation-json-malformed" for i in outcome.blockers)


def test_invalid_validation_status_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    load = load_validation_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "status": "bogus"})
    save_validation_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "invalid-validation-status" for i in outcome.blockers)


def test_result_present_for_non_terminal_status_is_a_warning(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "RESULT.md").write_text("## Outcome\n\nfoo\n", encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "result-present-for-non-terminal-status" and i.severity == "warning" for i in outcome.issues)


def test_result_missing_for_terminal_status_is_a_warning(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    load = load_task_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "status": "completed"})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "result-missing-for-terminal-status" and i.severity == "warning" for i in outcome.issues)


def test_path_escape_is_a_blocker(initialized_repo, monkeypatch):
    monkeypatch.setattr("forgeops.state.task_validate.task_dir_for", lambda repo_root, task_id: initialized_repo.parent / "escaped")
    outcome = validate_task(initialized_repo, "task-0001", True, _resolve_ok)
    assert any(i.key == "path-escape" for i in outcome.blockers)


def test_secret_like_content_in_spec_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    text = (task_dir / "SPEC.md").read_text(encoding="utf-8")
    (task_dir / "SPEC.md").write_text(text + "\nsecret: AKIAABCDEFGHIJKLMNOP\n", encoding="utf-8")  # forgeops:allow-secret
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "spec-secret-content" for i in outcome.blockers)


def test_accepted_checkpoint_unresolvable_is_a_warning_when_git_available(initialized_repo):
    task_id = _create(initialized_repo)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_none)
    assert any(i.key == "accepted-checkpoint-unresolvable" and i.severity == "warning" for i in outcome.issues)


def test_accepted_checkpoint_skipped_when_git_unavailable(initialized_repo):
    task_id = _create(initialized_repo)
    outcome = validate_task(initialized_repo, task_id, False, _resolve_none)
    assert not any(i.key == "accepted-checkpoint-unresolvable" for i in outcome.issues)


def test_warnings_and_blockers_are_separated(initialized_repo):
    task_id = _create(initialized_repo)
    _full_spec(initialized_repo, task_id)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "RESULT.md").write_text("## Outcome\n\nfoo\n", encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert outcome.has_blockers is False
    assert len(outcome.warnings) >= 1


# --- ownership consistency ----------------------------------------------------


def _create_worktree(repo_root, name="demo"):
    from forgeops.state.worktree_create import apply_worktree_create, build_worktree_create_plan
    plan = build_worktree_create_plan(repo_root, name, None, None)
    outcome = apply_worktree_create(repo_root, plan)
    assert outcome.ok is True
    return name


def _assign(repo_root, task_id, worktree_name):
    from forgeops.state.task_ownership import apply_task_assign, build_task_assign_plan
    plan = build_task_assign_plan(repo_root, task_id, worktree_name)
    outcome = apply_task_assign(repo_root, plan)
    assert outcome.ok is True


def test_valid_ownership_has_no_ownership_issues(initialized_repo):
    task_id = _create(initialized_repo)
    name = _create_worktree(initialized_repo)
    _assign(initialized_repo, task_id, name)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    ownership_keys = {
        "worktree-missing", "worktree-removed", "ownership-mismatch", "duplicate-assignment",
        "orphan-registry-ownership", "orphan-task-ownership", "worktree-id-schema-mismatch",
        "worktree-registry-malformed",
    }
    assert not any(i.key in ownership_keys for i in outcome.issues)


def test_missing_worktree_is_a_blocker(initialized_repo):
    from forgeops.state.task_registry import save_task_record
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    record = load_task_record(task_dir).record
    tampered = type(record)(**{**record.__dict__, "worktree_id": "no-such-worktree"})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "worktree-missing" for i in outcome.blockers)


def test_removed_worktree_is_a_blocker(initialized_repo):
    from forgeops.state.task_registry import save_task_record
    from forgeops.state.worktree_registry import STATUS_REMOVED, WorktreeRegistryDocument, load_registry, save_registry
    task_id = _create(initialized_repo)
    name = _create_worktree(initialized_repo)
    _assign(initialized_repo, task_id, name)
    registry = load_registry(initialized_repo)
    tampered_wt = type(registry.records[0])(**{**registry.records[0].__dict__, "status": STATUS_REMOVED})
    save_registry(initialized_repo, WorktreeRegistryDocument(records=[tampered_wt]))
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "worktree-removed" for i in outcome.blockers)


def test_mismatched_ownership_is_a_blocker(initialized_repo):
    from forgeops.state.worktree_registry import WorktreeRegistryDocument, load_registry, save_registry
    task_id1 = _create(initialized_repo, "Task One")
    task_id2 = _create(initialized_repo, "Task Two")
    name = _create_worktree(initialized_repo)
    _assign(initialized_repo, task_id1, name)
    # Tamper: point task2's TASK.json at the same worktree without updating the registry.
    from forgeops.state.task_registry import save_task_record
    task_dir2 = task_dir_for(initialized_repo, task_id2)
    record2 = load_task_record(task_dir2).record
    tampered2 = type(record2)(**{**record2.__dict__, "worktree_id": name})
    save_task_record(task_dir2, tampered2)
    outcome = validate_task(initialized_repo, task_id2, True, _resolve_ok)
    assert any(i.key == "ownership-mismatch" for i in outcome.blockers)


def test_orphan_task_ownership_is_a_blocker(initialized_repo):
    from forgeops.state.worktree_registry import WorktreeRegistryDocument, load_registry, save_registry
    task_id = _create(initialized_repo)
    name = _create_worktree(initialized_repo)
    _assign(initialized_repo, task_id, name)
    # Registry side forgets the assignment; task side still claims it.
    registry = load_registry(initialized_repo)
    tampered_wt = type(registry.records[0])(**{**registry.records[0].__dict__, "task_id": None})
    save_registry(initialized_repo, WorktreeRegistryDocument(records=[tampered_wt]))
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "orphan-task-ownership" for i in outcome.blockers)


def test_orphan_registry_ownership_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    name = _create_worktree(initialized_repo)
    _assign(initialized_repo, task_id, name)
    # Task side forgets the assignment; registry side still claims it.
    from forgeops.state.task_registry import save_task_record
    task_dir = task_dir_for(initialized_repo, task_id)
    record = load_task_record(task_dir).record
    tampered = type(record)(**{**record.__dict__, "worktree_id": None})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "orphan-registry-ownership" for i in outcome.blockers)


def test_duplicate_assignment_is_a_blocker(initialized_repo):
    from forgeops.state.worktree_registry import WorktreeRegistryDocument, load_registry, save_registry
    task_id = _create(initialized_repo)
    name1 = _create_worktree(initialized_repo, "demo1")
    name2 = _create_worktree(initialized_repo, "demo2")
    _assign(initialized_repo, task_id, name1)
    # Tamper the second worktree's record to also claim the same task.
    registry = load_registry(initialized_repo)
    records = list(registry.records)
    idx = next(i for i, r in enumerate(records) if r.name == name2)
    records[idx] = type(records[idx])(**{**records[idx].__dict__, "task_id": task_id})
    save_registry(initialized_repo, WorktreeRegistryDocument(records=records))
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "duplicate-assignment" for i in outcome.blockers)


def test_worktree_id_schema_mismatch_is_a_blocker(initialized_repo):
    from forgeops.state.task_registry import save_task_record
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    record = load_task_record(task_dir).record
    tampered = type(record)(**{**record.__dict__, "worktree_id": "has space"})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "worktree-id-schema-mismatch" for i in outcome.blockers)


def test_worktree_registry_malformed_is_a_blocker_for_ownership_check(initialized_repo):
    task_id = _create(initialized_repo)
    registry_path = initialized_repo / ".agent" / "runtime" / "WORKTREE_REGISTRY.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("{ not valid", encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "worktree-registry-malformed" for i in outcome.blockers)


def test_ownership_validate_never_mutates(initialized_repo):
    task_id = _create(initialized_repo)
    name = _create_worktree(initialized_repo)
    _assign(initialized_repo, task_id, name)
    before = set(initialized_repo.rglob("*"))
    validate_task(initialized_repo, task_id, True, _resolve_ok)
    after = set(initialized_repo.rglob("*"))
    assert before == after


# --- agent ownership consistency -----------------------------------------------


def _register_agent(repo_root, agent_id="claude-primary", kind="claude"):
    from forgeops.state.agent_register import apply_agent_register, build_agent_register_plan
    plan = build_agent_register_plan(repo_root, agent_id, kind, None)
    outcome = apply_agent_register(repo_root, plan)
    assert outcome.ok is True
    return agent_id


def _assign_agent(repo_root, task_id, agent_id):
    from forgeops.state.task_ownership import apply_task_assign_agent, build_task_assign_agent_plan
    plan = build_task_assign_agent_plan(repo_root, task_id, agent_id)
    outcome = apply_task_assign_agent(repo_root, plan)
    assert outcome.ok is True


def test_valid_agent_ownership_has_no_agent_issues(initialized_repo):
    task_id = _create(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    _assign_agent(initialized_repo, task_id, agent_id)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    agent_keys = {
        "agent-missing", "agent-registry-malformed", "agent-disabled-while-assigned",
        "agent-reciprocal-mismatch", "duplicate-agent-assignment", "orphan-task-agent-ownership",
        "orphan-registry-agent-ownership", "task-index-agent-mismatch", "invalid-agent-identifier",
        "terminal-task-with-agent",
    }
    assert not any(i.key in agent_keys for i in outcome.issues)


def test_missing_agent_is_a_blocker(initialized_repo):
    from forgeops.state.task_registry import save_task_record
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    record = load_task_record(task_dir).record
    tampered = type(record)(**{**record.__dict__, "agent_id": "no-such-agent"})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "agent-missing" for i in outcome.blockers)


def test_disabled_assigned_agent_is_a_blocker(initialized_repo):
    from forgeops.state.agent_registry import STATUS_DISABLED, AgentRegistryDocument, load_agent_registry, save_agent_registry
    task_id = _create(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    _assign_agent(initialized_repo, task_id, agent_id)
    registry = load_agent_registry(initialized_repo)
    tampered = type(registry.records[0])(**{**registry.records[0].__dict__, "status": STATUS_DISABLED})
    save_agent_registry(initialized_repo, AgentRegistryDocument(records=[tampered]))
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "agent-disabled-while-assigned" for i in outcome.blockers)


def test_agent_reciprocal_mismatch_is_a_blocker(initialized_repo):
    from forgeops.state.task_registry import save_task_record
    task_id1 = _create(initialized_repo, "Task One")
    task_id2 = _create(initialized_repo, "Task Two")
    agent_id = _register_agent(initialized_repo)
    _assign_agent(initialized_repo, task_id1, agent_id)
    # Tamper: point task2's TASK.json at the same agent without updating the registry.
    task_dir2 = task_dir_for(initialized_repo, task_id2)
    record2 = load_task_record(task_dir2).record
    tampered2 = type(record2)(**{**record2.__dict__, "agent_id": agent_id})
    save_task_record(task_dir2, tampered2)
    outcome = validate_task(initialized_repo, task_id2, True, _resolve_ok)
    assert any(i.key == "agent-reciprocal-mismatch" for i in outcome.blockers)


def test_duplicate_agent_assignment_is_a_blocker(initialized_repo):
    from forgeops.state.agent_registry import AgentRegistryDocument, load_agent_registry, save_agent_registry
    task_id = _create(initialized_repo)
    agent1 = _register_agent(initialized_repo, "claude-primary")
    agent2 = _register_agent(initialized_repo, "codex-reviewer", "codex")
    _assign_agent(initialized_repo, task_id, agent1)
    registry = load_agent_registry(initialized_repo)
    records = list(registry.records)
    idx = next(i for i, r in enumerate(records) if r.agent_id == agent2)
    records[idx] = type(records[idx])(**{**records[idx].__dict__, "assigned_task_id": task_id})
    save_agent_registry(initialized_repo, AgentRegistryDocument(records=records))
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "duplicate-agent-assignment" for i in outcome.blockers)


def test_orphan_task_agent_ownership_is_a_blocker(initialized_repo):
    from forgeops.state.agent_registry import AgentRegistryDocument, load_agent_registry, save_agent_registry
    task_id = _create(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    _assign_agent(initialized_repo, task_id, agent_id)
    registry = load_agent_registry(initialized_repo)
    tampered = type(registry.records[0])(**{**registry.records[0].__dict__, "assigned_task_id": None})
    save_agent_registry(initialized_repo, AgentRegistryDocument(records=[tampered]))
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "orphan-task-agent-ownership" for i in outcome.blockers)


def test_orphan_registry_agent_ownership_is_a_blocker(initialized_repo):
    from forgeops.state.task_registry import save_task_record
    task_id = _create(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    _assign_agent(initialized_repo, task_id, agent_id)
    task_dir = task_dir_for(initialized_repo, task_id)
    record = load_task_record(task_dir).record
    tampered = type(record)(**{**record.__dict__, "agent_id": None})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "orphan-registry-agent-ownership" for i in outcome.blockers)


def test_terminal_task_with_assigned_agent_is_a_blocker(initialized_repo):
    from forgeops.state.task_registry import STATUS_COMPLETED, save_task_record
    task_id = _create(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    _assign_agent(initialized_repo, task_id, agent_id)
    task_dir = task_dir_for(initialized_repo, task_id)
    record = load_task_record(task_dir).record
    tampered = type(record)(**{**record.__dict__, "status": STATUS_COMPLETED})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "terminal-task-with-agent" for i in outcome.blockers)


def test_invalid_agent_identifier_is_a_blocker(initialized_repo):
    from forgeops.state.task_registry import save_task_record
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    record = load_task_record(task_dir).record
    tampered = type(record)(**{**record.__dict__, "agent_id": "Has Spaces"})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "invalid-agent-identifier" for i in outcome.blockers)


def test_task_index_agent_mismatch_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    _assign_agent(initialized_repo, task_id, agent_id)
    import json
    index_path = initialized_repo / ".agent" / "tasks" / "TASK_INDEX.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["records"][0]["agent_id"] = "some-other-agent"
    index_path.write_text(json.dumps(payload), encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "task-index-agent-mismatch" for i in outcome.blockers)


def test_agent_registry_malformed_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    registry_path = initialized_repo / ".agent" / "agents" / "AGENT_REGISTRY.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("{ not valid", encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "agent-registry-malformed" for i in outcome.blockers)


def test_agent_ownership_validate_never_mutates(initialized_repo):
    task_id = _create(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    _assign_agent(initialized_repo, task_id, agent_id)
    before = set(initialized_repo.rglob("*"))
    validate_task(initialized_repo, task_id, True, _resolve_ok)
    after = set(initialized_repo.rglob("*"))
    assert before == after


# --- approval consistency -------------------------------------------------------


def _request_approval(repo_root, task_id, actor="joshua"):
    from forgeops.state.task_approval import apply_task_request_approval, build_task_request_approval_plan
    outcome = apply_task_request_approval(repo_root, build_task_request_approval_plan(repo_root, task_id, actor))
    assert outcome.ok is True


def _approve(repo_root, task_id, actor="joshua"):
    from forgeops.state.task_approval import apply_task_approve, build_task_approve_plan
    outcome = apply_task_approve(repo_root, build_task_approve_plan(repo_root, task_id, actor))
    assert outcome.ok is True


def _reject(repo_root, task_id, actor="joshua", reason="no"):
    from forgeops.state.task_approval import apply_task_reject, build_task_reject_plan
    outcome = apply_task_reject(repo_root, build_task_reject_plan(repo_root, task_id, actor, reason))
    assert outcome.ok is True


def test_fresh_task_has_no_approval_issues(initialized_repo):
    task_id = _create(initialized_repo)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    approval_keys = {i.key for i in outcome.issues if i.key.startswith("approval") or "approval" in i.key}
    assert approval_keys == set()


def test_valid_approval_lifecycle_has_no_approval_issues(initialized_repo):
    task_id = _create(initialized_repo)
    _request_approval(initialized_repo, task_id)
    _approve(initialized_repo, task_id)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    approval_keys = {i.key for i in outcome.issues if "approval" in i.key}
    assert approval_keys == set()


def test_task_index_approval_mismatch_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    _request_approval(initialized_repo, task_id)
    index_path = initialized_repo / ".agent" / "tasks" / "TASK_INDEX.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["records"][0]["approval_state"] = "not_requested"
    index_path.write_text(json.dumps(payload), encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "task-index-approval-mismatch" for i in outcome.blockers)


def test_malformed_approval_history_shape_reported_as_task_json_malformed(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    task_json_path = task_dir / "TASK.json"
    payload = json.loads(task_json_path.read_text(encoding="utf-8"))
    payload["approval_history"] = [{"actor": "joshua"}]  # missing required "action"
    task_json_path.write_text(json.dumps(payload), encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "task-json-malformed" for i in outcome.blockers)


def test_approval_history_invalid_action_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    _request_approval(initialized_repo, task_id)
    task_json_path = task_dir / "TASK.json"
    payload = json.loads(task_json_path.read_text(encoding="utf-8"))
    payload["approval_history"][0]["action"] = "bogus"
    task_json_path.write_text(json.dumps(payload), encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "approval-history-invalid-action" for i in outcome.blockers)


def test_approval_history_invalid_actor_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    _request_approval(initialized_repo, task_id)
    task_json_path = task_dir / "TASK.json"
    payload = json.loads(task_json_path.read_text(encoding="utf-8"))
    payload["approval_history"][0]["actor"] = ""
    task_json_path.write_text(json.dumps(payload), encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "approval-history-invalid-actor" for i in outcome.blockers)


def test_approval_history_secret_like_reason_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    _request_approval(initialized_repo, task_id)
    task_json_path = task_dir / "TASK.json"
    payload = json.loads(task_json_path.read_text(encoding="utf-8"))
    payload["approval_history"][0]["reason"] = "key is AKIAABCDEFGHIJKLMNOP"  # forgeops:allow-secret
    task_json_path.write_text(json.dumps(payload), encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "approval-history-secret-reason" for i in outcome.blockers)


def test_approval_history_invalid_timestamp_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    _request_approval(initialized_repo, task_id)
    task_json_path = task_dir / "TASK.json"
    payload = json.loads(task_json_path.read_text(encoding="utf-8"))
    payload["approval_history"][0]["timestamp"] = "not-a-timestamp"
    task_json_path.write_text(json.dumps(payload), encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "approval-history-invalid-timestamp" for i in outcome.blockers)


def test_approval_history_missing_required_reason_for_rejection_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    _request_approval(initialized_repo, task_id)
    _reject(initialized_repo, task_id)
    task_json_path = task_dir / "TASK.json"
    payload = json.loads(task_json_path.read_text(encoding="utf-8"))
    payload["approval_history"][-1]["reason"] = ""
    task_json_path.write_text(json.dumps(payload), encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "approval-history-missing-required-reason" for i in outcome.blockers)


def test_approval_history_oversized_reason_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    _request_approval(initialized_repo, task_id)
    task_json_path = task_dir / "TASK.json"
    payload = json.loads(task_json_path.read_text(encoding="utf-8"))
    payload["approval_history"][0]["reason"] = "x" * 3000
    task_json_path.write_text(json.dumps(payload), encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "approval-history-oversized-reason" for i in outcome.blockers)


def test_approval_state_history_mismatch_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    _request_approval(initialized_repo, task_id)
    _approve(initialized_repo, task_id)
    task_json_path = task_dir / "TASK.json"
    payload = json.loads(task_json_path.read_text(encoding="utf-8"))
    payload["approval_state"] = "pending"  # history replay says 'approved'
    task_json_path.write_text(json.dumps(payload), encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "approval-state-history-mismatch" for i in outcome.blockers)


def test_approval_history_impossible_transition_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    _request_approval(initialized_repo, task_id)
    _approve(initialized_repo, task_id)
    task_json_path = task_dir / "TASK.json"
    payload = json.loads(task_json_path.read_text(encoding="utf-8"))
    # Duplicate the 'approved' event - approved -> approved is not a valid transition.
    payload["approval_history"].append(dict(payload["approval_history"][-1]))
    task_json_path.write_text(json.dumps(payload), encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "approval-history-impossible-transition" for i in outcome.blockers)


def test_terminal_task_with_pending_approval_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    _request_approval(initialized_repo, task_id)
    record = load_task_record(task_dir).record
    tampered = type(record)(**{**record.__dict__, "status": "completed"})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "terminal-task-with-pending-approval" for i in outcome.blockers)


def test_approval_validate_never_mutates(initialized_repo):
    task_id = _create(initialized_repo)
    _request_approval(initialized_repo, task_id)
    _approve(initialized_repo, task_id)
    before = set(initialized_repo.rglob("*"))
    validate_task(initialized_repo, task_id, True, _resolve_ok)
    after = set(initialized_repo.rglob("*"))
    assert before == after
