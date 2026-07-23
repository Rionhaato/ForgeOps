"""Integration tests for `forgeops agent register|list|show`
(forgeops/cli/agent.py). See docs/agents.md for the registry contract,
declarative-identity-only model, and confirmation semantics this
exercises end-to-end through the CLI entry points."""
from __future__ import annotations

import json
import subprocess

from forgeops.cli.agent import render_human, run_agent_list, run_agent_register, run_agent_show
from forgeops.core import exit_codes
from forgeops.state.agent_registry import load_agent_registry


def _register(repo, agent_id="claude-primary", kind="claude", **kwargs):
    result = run_agent_register(agent_id, kind, str(repo), write_log=False, **kwargs)
    assert result.exit_code == exit_codes.SUCCESS
    return result


# --- registration --------------------------------------------------------------


def test_register_success(initialized_repo):
    result = _register(initialized_repo)
    assert result.data["agent_id"] == "claude-primary"
    registry = load_agent_registry(initialized_repo)
    assert len(registry.records) == 1


def test_register_dry_run_zero_mutation(initialized_repo):
    before = set(initialized_repo.rglob("*"))
    result = run_agent_register("claude-primary", "claude", str(initialized_repo), write_log=False, dry_run=True)
    after = set(initialized_repo.rglob("*"))
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["action"] == "would_register"
    assert before == after


def test_register_human_json_agree(initialized_repo):
    result = _register(initialized_repo)
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops agent register" in rendered
    assert payload["data"]["agent_id"] == "claude-primary"
    assert payload["exit_code"] == result.exit_code


def test_register_each_supported_kind(initialized_repo):
    for i, kind in enumerate(["claude", "codex", "specialist", "rocky"]):
        result = run_agent_register(f"agent-{i}", kind, str(initialized_repo), write_log=False)
        assert result.exit_code == exit_codes.SUCCESS, kind


def test_register_duplicate_id_refused(initialized_repo):
    _register(initialized_repo)
    result = run_agent_register("claude-primary", "codex", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "duplicate-agent-id" for c in result.data["conflicts"])


def test_register_invalid_id_refused(initialized_repo):
    result = run_agent_register("Has Spaces", "claude", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-agent-id" for c in result.data["conflicts"])


def test_register_secret_like_id_refused(initialized_repo):
    result = run_agent_register("sk-abcdefghijklmnopqrstuvwxyz", "claude", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "agent-id-secret-detected" for c in result.data["conflicts"])


def test_register_secret_like_display_name_refused(initialized_repo):
    result = run_agent_register(
        "claude-primary", "claude", str(initialized_repo), write_log=False,
        display_name="key: AKIAABCDEFGHIJKLMNOP",
    )
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "display-name-secret-detected" for c in result.data["conflicts"])


def test_register_malformed_registry_fails_closed(initialized_repo):
    registry_path = initialized_repo / ".agent" / "agents" / "AGENT_REGISTRY.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{ not valid", encoding="utf-8")
    result = run_agent_register("claude-primary", "claude", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "agent-registry-malformed" for c in result.data["conflicts"])
    assert registry_path.read_text(encoding="utf-8") == "{ not valid"


def test_register_uninitialized_project_rejected(git_repo):
    result = run_agent_register("claude-primary", "claude", str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED


def test_register_protected_target_rejected(tmp_path, monkeypatch):
    fake_reference = tmp_path / "FakeTrendForge"
    fake_reference.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(fake_reference), check=True)
    monkeypatch.setattr("forgeops.core.paths.READONLY_REFERENCE_REPO", fake_reference)
    result = run_agent_register("claude-primary", "claude", str(fake_reference), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED


def test_register_spacey_path(spacey_initialized_repo):
    result = _register(spacey_initialized_repo)
    assert result.exit_code == exit_codes.SUCCESS
    registry = load_agent_registry(spacey_initialized_repo)
    assert len(registry.records) == 1


def test_register_atomic_write_failure(initialized_repo, monkeypatch):
    from forgeops.state.agent_register import AgentRegisterOutcome
    monkeypatch.setattr(
        "forgeops.cli.agent.apply_agent_register",
        lambda repo_root, plan, clock=None: AgentRegisterOutcome(ok=False, agent_id=None, partial_state={"error": "simulated failure"}),
    )
    result = run_agent_register("claude-primary", "claude", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    assert result.data["partial_state"] is not None
    assert "manual_recovery_recommendation" in result.data


def test_register_no_git_executable(initialized_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.agent.git_version", lambda: None)
    result = run_agent_register("claude-primary", "claude", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE


# --- list / show -----------------------------------------------------------------


def test_list_empty(initialized_repo):
    result = run_agent_list(str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["agents"] == []


def test_list_multiple(initialized_repo):
    _register(initialized_repo, "claude-primary", "claude")
    _register(initialized_repo, "codex-reviewer", "codex")
    result = run_agent_list(str(initialized_repo), write_log=False)
    assert result.data["total"] == 2


def test_list_kind_filtering(initialized_repo):
    _register(initialized_repo, "claude-primary", "claude")
    _register(initialized_repo, "codex-reviewer", "codex")
    result = run_agent_list(str(initialized_repo), write_log=False, kind_filter="codex")
    assert result.data["total"] == 1
    assert result.data["agents"][0]["agent_id"] == "codex-reviewer"


def test_list_malformed_registry_reported_not_mutated(initialized_repo):
    registry_path = initialized_repo / ".agent" / "agents" / "AGENT_REGISTRY.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{ not valid", encoding="utf-8")
    result = run_agent_list(str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    assert registry_path.read_text(encoding="utf-8") == "{ not valid"


def test_list_concise_human_output(initialized_repo):
    _register(initialized_repo)
    result = run_agent_list(str(initialized_repo), write_log=False)
    rendered = render_human(result)
    assert "claude-primary" in rendered
    assert "[claude]" in rendered


def test_list_json_output(initialized_repo):
    _register(initialized_repo)
    result = run_agent_list(str(initialized_repo), write_log=False)
    payload = json.loads(result.to_json())
    assert payload["command"] == "agent-list"


def test_list_read_only_no_mutation(initialized_repo):
    _register(initialized_repo)
    before = set(initialized_repo.rglob("*"))
    run_agent_list(str(initialized_repo), write_log=False)
    after = set(initialized_repo.rglob("*"))
    assert before == after


def test_show_known_agent(initialized_repo):
    _register(initialized_repo, display_name="Primary Claude")
    result = run_agent_show("claude-primary", str(initialized_repo), write_log=False)
    assert result.data["found"] is True
    assert result.data["agent"]["display_name"] == "Primary Claude"


def test_show_unknown_agent(initialized_repo):
    result = run_agent_show("no-such-agent", str(initialized_repo), write_log=False)
    assert result.data["found"] is False
    assert result.exit_code == exit_codes.BLOCKED


def test_show_malformed_registry(initialized_repo):
    registry_path = initialized_repo / ".agent" / "agents" / "AGENT_REGISTRY.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text("{ not valid", encoding="utf-8")
    result = run_agent_show("claude-primary", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert registry_path.read_text(encoding="utf-8") == "{ not valid"


def test_show_human_json_agree(initialized_repo):
    _register(initialized_repo)
    result = run_agent_show("claude-primary", str(initialized_repo), write_log=False)
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops agent show" in rendered
    assert payload["data"]["agent"]["agent_id"] == "claude-primary"


def test_show_read_only_no_mutation(initialized_repo):
    _register(initialized_repo)
    before = set(initialized_repo.rglob("*"))
    run_agent_show("claude-primary", str(initialized_repo), write_log=False)
    after = set(initialized_repo.rglob("*"))
    assert before == after


def test_show_no_secret_content_exposed(initialized_repo):
    _register(initialized_repo)
    result = run_agent_show("claude-primary", str(initialized_repo), write_log=False)
    assert "AKIA" not in result.to_json()
