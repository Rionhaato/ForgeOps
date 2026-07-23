"""Tests for forgeops/state/agent_registry.py: AGENT_REGISTRY.json
load/save and agent-ID validation."""
from __future__ import annotations

import json

from forgeops.state.agent_registry import (
    AGENT_REGISTRY_RELATIVE_PATH,
    STATUS_DISABLED,
    STATUS_REGISTERED,
    AgentRecord,
    AgentRegistryDocument,
    load_agent_registry,
    save_agent_registry,
    validate_agent_id,
)


def _record(agent_id="claude-primary", kind="claude"):
    return AgentRecord(
        agent_id=agent_id, kind=kind, display_name=agent_id,
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )


# --- validate_agent_id --------------------------------------------------------


def test_valid_agent_ids():
    assert validate_agent_id("claude-primary") is None
    assert validate_agent_id("codex_reviewer") is None
    assert validate_agent_id("security-specialist") is None
    assert validate_agent_id("a") is None


def test_agent_id_must_not_be_empty():
    assert validate_agent_id("") is not None
    assert validate_agent_id("   ") is not None


def test_agent_id_must_not_be_absolute():
    assert validate_agent_id("C:\\agent") is not None


def test_agent_id_must_be_lowercase():
    assert validate_agent_id("Claude-Primary") is not None


def test_agent_id_rejects_traversal_and_separators():
    assert validate_agent_id("../escape") is not None
    assert validate_agent_id("a/b") is not None
    assert validate_agent_id("a\\b") is not None
    assert validate_agent_id("has space") is not None


def test_agent_id_must_start_with_letter_or_digit():
    assert validate_agent_id("-claude") is not None
    assert validate_agent_id("_claude") is not None


def test_agent_id_length_limit():
    assert validate_agent_id("a" * 65) is not None
    assert validate_agent_id("a" * 64) is None


# --- AGENT_REGISTRY.json -------------------------------------------------------


def test_missing_registry_returns_empty(tmp_path):
    doc = load_agent_registry(tmp_path)
    assert doc.records == []
    assert doc.warning is None


def test_save_then_load_round_trips(tmp_path):
    save_agent_registry(tmp_path, AgentRegistryDocument(records=[_record()]))
    doc = load_agent_registry(tmp_path)
    assert len(doc.records) == 1
    assert doc.records[0].agent_id == "claude-primary"
    assert doc.records[0].status == STATUS_REGISTERED
    assert doc.records[0].assigned_task_id is None
    assert doc.records[0].capabilities == []
    assert doc.records[0].metadata == {}
    assert doc.warning is None


def test_save_writes_to_documented_path(tmp_path):
    save_agent_registry(tmp_path, AgentRegistryDocument(records=[_record()]))
    assert (tmp_path / AGENT_REGISTRY_RELATIVE_PATH).is_file()


def test_save_is_atomic_write_valid_json(tmp_path):
    save_agent_registry(tmp_path, AgentRegistryDocument(records=[_record()]))
    content = (tmp_path / AGENT_REGISTRY_RELATIVE_PATH).read_text(encoding="utf-8")
    json.loads(content)


def test_invalid_json_returns_malformed(tmp_path):
    path = tmp_path / AGENT_REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text("{ not valid", encoding="utf-8")
    doc = load_agent_registry(tmp_path)
    assert doc.records == []
    assert doc.warning is not None


def test_non_object_top_level_returns_malformed(tmp_path):
    path = tmp_path / AGENT_REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text("[1,2,3]", encoding="utf-8")
    doc = load_agent_registry(tmp_path)
    assert doc.warning is not None


def test_unsupported_schema_version_returns_malformed(tmp_path):
    path = tmp_path / AGENT_REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 999, "records": []}), encoding="utf-8")
    doc = load_agent_registry(tmp_path)
    assert doc.warning is not None


def test_records_not_a_list_returns_malformed(tmp_path):
    path = tmp_path / AGENT_REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "records": "nope"}), encoding="utf-8")
    doc = load_agent_registry(tmp_path)
    assert doc.warning is not None


def test_one_bad_record_marks_whole_document_malformed(tmp_path):
    path = tmp_path / AGENT_REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "records": [_record().to_dict(), {"kind": "missing agent_id entirely"}],
    }), encoding="utf-8")
    doc = load_agent_registry(tmp_path)
    assert doc.records == []
    assert doc.warning is not None


def test_never_stores_secret_shaped_content(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_API_TOKEN", "super-secret-leak")  # forgeops:allow-secret
    save_agent_registry(tmp_path, AgentRegistryDocument(records=[_record()]))
    content = (tmp_path / AGENT_REGISTRY_RELATIVE_PATH).read_text(encoding="utf-8")
    assert "super-secret-leak" not in content


def test_repo_path_with_spaces(tmp_path):
    spacey = tmp_path / "a repo with spaces"
    spacey.mkdir()
    save_agent_registry(spacey, AgentRegistryDocument(records=[_record()]))
    doc = load_agent_registry(spacey)
    assert len(doc.records) == 1


def test_disabled_status_round_trips():
    record = AgentRecord(
        agent_id="x", kind="claude", display_name="x", status=STATUS_DISABLED,
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )
    restored = AgentRecord.from_dict(record.to_dict())
    assert restored.status == STATUS_DISABLED


def test_metadata_and_capabilities_round_trip():
    record = AgentRecord(
        agent_id="x", kind="claude", display_name="x",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        capabilities=["reviews-code"], metadata={"note": "trial"},
    )
    restored = AgentRecord.from_dict(record.to_dict())
    assert restored.capabilities == ["reviews-code"]
    assert restored.metadata == {"note": "trial"}
