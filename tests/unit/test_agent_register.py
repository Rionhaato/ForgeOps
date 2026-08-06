"""Tests for forgeops/state/agent_register.py: the read-only preflight
plan builder and the single mutating apply step behind `forgeops agent
register`."""
from __future__ import annotations

import json

from forgeops.state.agent_register import apply_agent_register, build_agent_register_plan
from forgeops.state.agent_registry import RECOGNIZED_AGENT_KINDS, load_agent_registry


def test_clean_plan_has_no_conflicts(initialized_repo):
    plan = build_agent_register_plan(initialized_repo, "claude-primary", "claude", None)
    assert plan.has_conflict is False
    assert plan.display_name == "claude-primary"


def test_build_plan_never_writes_anything(initialized_repo):
    before = set(initialized_repo.rglob("*"))
    build_agent_register_plan(initialized_repo, "claude-primary", "claude", None)
    after = set(initialized_repo.rglob("*"))
    assert before == after


def test_every_supported_kind(initialized_repo):
    for kind in sorted(RECOGNIZED_AGENT_KINDS):
        plan = build_agent_register_plan(initialized_repo, f"agent-{kind}", kind, None)
        assert plan.has_conflict is False, kind


def test_not_initialized_project_is_a_conflict(git_repo):
    plan = build_agent_register_plan(git_repo, "claude-primary", "claude", None)
    assert any(c.key == "not-initialized" for c in plan.conflicts)


def test_protected_reference_repo_is_a_conflict(initialized_repo, monkeypatch):
    monkeypatch.setattr("forgeops.state.agent_register.is_protected_reference_path", lambda p: True)
    plan = build_agent_register_plan(initialized_repo, "claude-primary", "claude", None)
    assert any(c.key == "protected-reference-repo" for c in plan.conflicts)


def test_invalid_kind_is_a_conflict(initialized_repo):
    plan = build_agent_register_plan(initialized_repo, "claude-primary", "bogus-kind", None)
    assert any(c.key == "invalid-kind" for c in plan.conflicts)


def test_invalid_agent_id_is_a_conflict(initialized_repo):
    plan = build_agent_register_plan(initialized_repo, "Has Spaces", "claude", None)
    assert any(c.key == "invalid-agent-id" for c in plan.conflicts)


def test_duplicate_agent_id_is_a_conflict(initialized_repo):
    plan = build_agent_register_plan(initialized_repo, "claude-primary", "claude", None)
    apply_agent_register(initialized_repo, plan)
    plan2 = build_agent_register_plan(initialized_repo, "claude-primary", "claude", None)
    assert any(c.key == "duplicate-agent-id" for c in plan2.conflicts)


def test_secret_like_display_name_is_a_conflict(initialized_repo):
    plan = build_agent_register_plan(initialized_repo, "claude-primary", "claude", "key: AKIAABCDEFGHIJKLMNOP")  # forgeops:allow-secret
    assert any(c.key == "display-name-secret-detected" for c in plan.conflicts)


def test_oversized_display_name_is_a_conflict(initialized_repo):
    plan = build_agent_register_plan(initialized_repo, "claude-primary", "claude", "x" * 500)
    assert any(c.key == "invalid-display-name" for c in plan.conflicts)


def test_malformed_registry_is_a_conflict(initialized_repo):
    registry_path = initialized_repo / ".agent" / "agents" / "AGENT_REGISTRY.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{ not valid", encoding="utf-8")
    plan = build_agent_register_plan(initialized_repo, "claude-primary", "claude", None)
    assert any(c.key == "agent-registry-malformed" for c in plan.conflicts)


def test_display_name_defaults_to_agent_id(initialized_repo):
    plan = build_agent_register_plan(initialized_repo, "claude-primary", "claude", None)
    assert plan.display_name == "claude-primary"


def test_apply_registers_agent(initialized_repo):
    plan = build_agent_register_plan(initialized_repo, "claude-primary", "claude", "Primary Claude")
    outcome = apply_agent_register(initialized_repo, plan)
    assert outcome.ok is True
    assert outcome.agent_id == "claude-primary"
    registry = load_agent_registry(initialized_repo)
    assert len(registry.records) == 1
    record = registry.records[0]
    assert record.agent_id == "claude-primary"
    assert record.kind == "claude"
    assert record.display_name == "Primary Claude"
    assert record.status == "registered"
    assert record.capabilities == []
    assert record.assigned_task_id is None
    assert record.metadata == {}


def test_apply_spacey_repo_path(spacey_initialized_repo):
    plan = build_agent_register_plan(spacey_initialized_repo, "claude-primary", "claude", None)
    outcome = apply_agent_register(spacey_initialized_repo, plan)
    assert outcome.ok is True
    registry = load_agent_registry(spacey_initialized_repo)
    assert len(registry.records) == 1


def test_apply_registry_is_valid_json(initialized_repo):
    plan = build_agent_register_plan(initialized_repo, "claude-primary", "claude", None)
    apply_agent_register(initialized_repo, plan)
    registry_path = initialized_repo / ".agent" / "agents" / "AGENT_REGISTRY.json"
    json.loads(registry_path.read_text(encoding="utf-8"))


def test_apply_multiple_registrations(initialized_repo):
    plan1 = build_agent_register_plan(initialized_repo, "claude-primary", "claude", None)
    apply_agent_register(initialized_repo, plan1)
    plan2 = build_agent_register_plan(initialized_repo, "codex-reviewer", "codex", None)
    outcome2 = apply_agent_register(initialized_repo, plan2)
    assert outcome2.ok is True
    registry = load_agent_registry(initialized_repo)
    assert {r.agent_id for r in registry.records} == {"claude-primary", "codex-reviewer"}


def test_apply_simulated_write_failure(initialized_repo, monkeypatch):
    plan = build_agent_register_plan(initialized_repo, "claude-primary", "claude", None)
    monkeypatch.setattr(
        "forgeops.state.agent_register.save_agent_registry",
        lambda *a, **k: (_ for _ in ()).throw(OSError("simulated disk failure")),
    )
    outcome = apply_agent_register(initialized_repo, plan)
    assert outcome.ok is False
    assert outcome.partial_state is not None
    registry = load_agent_registry(initialized_repo)
    assert registry.records == []


def test_apply_toctou_duplicate_registered_between_preflight_and_apply(initialized_repo):
    plan = build_agent_register_plan(initialized_repo, "claude-primary", "claude", None)
    assert not plan.has_conflict
    # Someone else registers the same ID in the meantime.
    other_plan = build_agent_register_plan(initialized_repo, "claude-primary", "codex", None)
    apply_agent_register(initialized_repo, other_plan)
    outcome = apply_agent_register(initialized_repo, plan)
    assert outcome.ok is False
    registry = load_agent_registry(initialized_repo)
    assert len(registry.records) == 1
    assert registry.records[0].kind == "codex"  # the first (other) registration, never overwritten
