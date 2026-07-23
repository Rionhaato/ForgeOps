"""Tests for forgeops/state/worktree_registry.py:
.agent/runtime/WORKTREE_REGISTRY.json load/save. Unlike
forgeops.state.runtime_registry (which skips an individual malformed
record), this registry treats *any* unreadable record as the whole
document being malformed - see module docstring for why: worktree
conflict-detection needs to trust that "no matching record" really
means "no matching record", not "a record existed but we silently
dropped it"."""
from __future__ import annotations

import json

from forgeops.state.worktree_registry import (
    REGISTRY_RELATIVE_PATH,
    WorktreeRecord,
    WorktreeRegistryDocument,
    load_registry,
    save_registry,
)


def _record(id_="abc123", name="demo", path="C:\\repo\\.forgeops-worktrees\\repo\\demo"):
    return WorktreeRecord(
        id=id_, name=name, path=path, branch="forgeops/demo",
        base_commit="deadbeef", created_at="2026-01-01T00:00:00Z",
    )


def test_missing_registry_returns_empty(tmp_path):
    doc = load_registry(tmp_path)
    assert doc.records == []
    assert doc.warning is None


def test_save_then_load_round_trips(tmp_path):
    save_registry(tmp_path, WorktreeRegistryDocument(records=[_record()]))
    doc = load_registry(tmp_path)
    assert len(doc.records) == 1
    assert doc.records[0].id == "abc123"
    assert doc.records[0].task_id is None
    assert doc.records[0].agent_id is None
    assert doc.warning is None


def test_save_writes_to_documented_path(tmp_path):
    save_registry(tmp_path, WorktreeRegistryDocument(records=[_record()]))
    assert (tmp_path / REGISTRY_RELATIVE_PATH).is_file()


def test_save_is_atomic_write_valid_json(tmp_path):
    save_registry(tmp_path, WorktreeRegistryDocument(records=[_record()]))
    content = (tmp_path / REGISTRY_RELATIVE_PATH).read_text(encoding="utf-8")
    json.loads(content)  # must not raise


def test_invalid_json_returns_malformed_with_warning(tmp_path):
    path = tmp_path / REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid", encoding="utf-8")
    doc = load_registry(tmp_path)
    assert doc.records == []
    assert doc.warning is not None


def test_non_object_top_level_returns_malformed_with_warning(tmp_path):
    path = tmp_path / REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1,2,3]", encoding="utf-8")
    doc = load_registry(tmp_path)
    assert doc.warning is not None


def test_unsupported_schema_version_returns_malformed_with_warning(tmp_path):
    path = tmp_path / REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 999, "records": []}), encoding="utf-8")
    doc = load_registry(tmp_path)
    assert doc.warning is not None


def test_records_not_a_list_returns_malformed_with_warning(tmp_path):
    path = tmp_path / REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "records": "not-a-list"}), encoding="utf-8")
    doc = load_registry(tmp_path)
    assert doc.warning is not None


def test_one_bad_record_marks_whole_document_malformed(tmp_path):
    path = tmp_path / REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "records": [
            _record(id_="good").to_dict(),
            {"name": "missing id field entirely"},
        ],
    }), encoding="utf-8")
    doc = load_registry(tmp_path)
    # Deliberately fails closed rather than silently keeping "good" and
    # dropping the unreadable one - a caller must not trust an empty
    # match here to mean "definitely no conflicting record".
    assert doc.records == []
    assert doc.warning is not None


def test_never_stores_secret_shaped_content(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_API_TOKEN", "super-secret-leak")  # forgeops:allow-secret
    save_registry(tmp_path, WorktreeRegistryDocument(records=[_record()]))
    content = (tmp_path / REGISTRY_RELATIVE_PATH).read_text(encoding="utf-8")
    assert "super-secret-leak" not in content


def test_repo_path_with_spaces(tmp_path):
    spacey = tmp_path / "a repo with spaces"
    spacey.mkdir()
    save_registry(spacey, WorktreeRegistryDocument(records=[_record()]))
    doc = load_registry(spacey)
    assert len(doc.records) == 1


def test_task_and_agent_ownership_fields_round_trip_when_set():
    record = WorktreeRecord(
        id="x", name="n", path="p", branch="b", base_commit="c",
        created_at="2026-01-01T00:00:00Z", task_id="t1", agent_id="a1",
    )
    restored = WorktreeRecord.from_dict(record.to_dict())
    assert restored.task_id == "t1"
    assert restored.agent_id == "a1"
