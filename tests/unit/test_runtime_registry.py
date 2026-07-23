"""Tests for forgeops/state/runtime_registry.py: the ForgeOps process-
ownership registry (.agent/runtime/PROCESS_REGISTRY.json)."""
from __future__ import annotations

import json

from forgeops.state.runtime_registry import (
    REGISTRY_RELATIVE_PATH,
    RegistryDocument,
    RegistryRecord,
    load_registry,
    save_registry,
)


def _record(pid=1, category="backend-dev-server", repo="C:\\repo"):
    return RegistryRecord(
        pid=pid, category=category, repository_root=repo,
        start_time_utc="2026-01-01T00:00:00Z", command_fingerprint="x", creation_source="test",
    )


def test_missing_registry_returns_empty(tmp_path):
    doc = load_registry(tmp_path)
    assert doc.records == []
    assert doc.warning is None


def test_save_then_load_round_trips(tmp_path):
    save_registry(tmp_path, RegistryDocument(records=[_record(pid=42)]))
    doc = load_registry(tmp_path)
    assert len(doc.records) == 1
    assert doc.records[0].pid == 42
    assert doc.warning is None


def test_save_writes_to_documented_path(tmp_path):
    save_registry(tmp_path, RegistryDocument(records=[_record()]))
    assert (tmp_path / REGISTRY_RELATIVE_PATH).is_file()


def test_save_is_atomic_write_valid_json(tmp_path):
    save_registry(tmp_path, RegistryDocument(records=[_record()]))
    content = (tmp_path / REGISTRY_RELATIVE_PATH).read_text(encoding="utf-8")
    json.loads(content)  # must not raise


def test_invalid_json_returns_empty_with_warning(tmp_path):
    path = tmp_path / REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid", encoding="utf-8")
    doc = load_registry(tmp_path)
    assert doc.records == []
    assert doc.warning is not None


def test_non_object_top_level_returns_empty_with_warning(tmp_path):
    path = tmp_path / REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1,2,3]", encoding="utf-8")
    doc = load_registry(tmp_path)
    assert doc.records == []
    assert doc.warning is not None


def test_unsupported_schema_version_returns_empty_with_warning(tmp_path):
    path = tmp_path / REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 999, "records": []}), encoding="utf-8")
    doc = load_registry(tmp_path)
    assert doc.records == []
    assert doc.warning is not None


def test_records_not_a_list_returns_empty_with_warning(tmp_path):
    path = tmp_path / REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "records": "not-a-list"}), encoding="utf-8")
    doc = load_registry(tmp_path)
    assert doc.records == []
    assert doc.warning is not None


def test_malformed_individual_record_is_skipped_not_fatal(tmp_path):
    path = tmp_path / REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "records": [
            {"pid": 1, "category": "backend-dev-server", "repository_root": "C:\\repo", "command_fingerprint": "x", "creation_source": "test"},
            {"category": "missing pid field entirely"},
            "not even a dict",
        ],
    }), encoding="utf-8")
    doc = load_registry(tmp_path)
    assert len(doc.records) == 1
    assert doc.records[0].pid == 1


def test_never_stores_secret_shaped_content(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_API_TOKEN", "super-secret-leak")  # forgeops:allow-secret
    save_registry(tmp_path, RegistryDocument(records=[_record()]))
    content = (tmp_path / REGISTRY_RELATIVE_PATH).read_text(encoding="utf-8")
    assert "super-secret-leak" not in content


def test_repo_path_with_spaces(tmp_path):
    spacey = tmp_path / "a repo with spaces"
    spacey.mkdir()
    save_registry(spacey, RegistryDocument(records=[_record()]))
    doc = load_registry(spacey)
    assert len(doc.records) == 1
