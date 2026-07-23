"""Tests for forgeops/state/task_registry.py: TASK_INDEX.json load/save,
TASK.json/VALIDATION.json load/save, and task-ID validation."""
from __future__ import annotations

import json

from forgeops.state.task_registry import (
    INDEX_RELATIVE_PATH,
    RESULT_STATE_PENDING,
    VALIDATION_STATUS_NOT_RUN,
    TaskIndexDocument,
    TaskIndexRecord,
    TaskRecord,
    ValidationRecord,
    default_validation_record,
    load_index,
    load_task_record,
    load_validation_record,
    save_index,
    save_task_record,
    save_validation_record,
    task_dir_for,
    validate_task_id,
)


def _index_record(task_id="task-0001"):
    return TaskIndexRecord(
        task_id=task_id, title="demo", status="draft",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        path=f".agent/tasks/{task_id}",
    )


def _task_record(task_id="task-0001"):
    return TaskRecord(
        task_id=task_id, title="demo", status="draft", project_root="C:\\repo",
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
        created_by="forgeops-cli", scope_summary="demo (see SPEC.md for full scope)",
        accepted_checkpoint="deadbeef", source_branch="main", source_head="deadbeef",
    )


# --- validate_task_id --------------------------------------------------------


def test_valid_task_id():
    assert validate_task_id("task-0001") is None
    assert validate_task_id("task-99999") is None


def test_task_id_must_not_be_empty():
    assert validate_task_id("") is not None


def test_task_id_must_not_be_absolute():
    assert validate_task_id("C:\\task-0001") is not None


def test_task_id_must_match_pattern():
    assert validate_task_id("0001") is not None
    assert validate_task_id("task-1") is not None  # too few digits
    assert validate_task_id("task-0001/../escape") is not None
    assert validate_task_id("../task-0001") is not None


def test_task_dir_for():
    from pathlib import Path
    assert task_dir_for(Path("C:\\repo"), "task-0001") == Path("C:\\repo") / ".agent" / "tasks" / "task-0001"


# --- TASK_INDEX.json ---------------------------------------------------------


def test_missing_index_returns_empty(tmp_path):
    doc = load_index(tmp_path)
    assert doc.records == []
    assert doc.next_task_number == 1
    assert doc.warning is None


def test_save_then_load_round_trips(tmp_path):
    save_index(tmp_path, TaskIndexDocument(next_task_number=2, records=[_index_record()]))
    doc = load_index(tmp_path)
    assert doc.next_task_number == 2
    assert len(doc.records) == 1
    assert doc.records[0].task_id == "task-0001"
    assert doc.warning is None


def test_save_writes_to_documented_path(tmp_path):
    save_index(tmp_path, TaskIndexDocument(records=[_index_record()]))
    assert (tmp_path / INDEX_RELATIVE_PATH).is_file()


def test_save_is_atomic_write_valid_json(tmp_path):
    save_index(tmp_path, TaskIndexDocument(records=[_index_record()]))
    content = (tmp_path / INDEX_RELATIVE_PATH).read_text(encoding="utf-8")
    json.loads(content)


def test_invalid_json_returns_malformed(tmp_path):
    path = tmp_path / INDEX_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text("{ not valid", encoding="utf-8")
    doc = load_index(tmp_path)
    assert doc.records == []
    assert doc.warning is not None


def test_non_object_top_level_returns_malformed(tmp_path):
    path = tmp_path / INDEX_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text("[1,2,3]", encoding="utf-8")
    doc = load_index(tmp_path)
    assert doc.warning is not None


def test_unsupported_schema_version_returns_malformed(tmp_path):
    path = tmp_path / INDEX_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 999, "next_task_number": 1, "records": []}), encoding="utf-8")
    doc = load_index(tmp_path)
    assert doc.warning is not None


def test_missing_next_task_number_returns_malformed(tmp_path):
    path = tmp_path / INDEX_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "records": []}), encoding="utf-8")
    doc = load_index(tmp_path)
    assert doc.warning is not None


def test_records_not_a_list_returns_malformed(tmp_path):
    path = tmp_path / INDEX_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "next_task_number": 1, "records": "nope"}), encoding="utf-8")
    doc = load_index(tmp_path)
    assert doc.warning is not None


def test_one_bad_record_marks_whole_document_malformed(tmp_path):
    path = tmp_path / INDEX_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema_version": 1, "next_task_number": 2,
        "records": [_index_record().to_dict(), {"title": "missing task_id"}],
    }), encoding="utf-8")
    doc = load_index(tmp_path)
    assert doc.records == []
    assert doc.warning is not None


def test_never_stores_secret_shaped_content(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_API_TOKEN", "super-secret-leak")  # forgeops:allow-secret
    save_index(tmp_path, TaskIndexDocument(records=[_index_record()]))
    content = (tmp_path / INDEX_RELATIVE_PATH).read_text(encoding="utf-8")
    assert "super-secret-leak" not in content


# --- TASK.json -----------------------------------------------------------


def test_task_record_round_trips(tmp_path):
    task_dir = tmp_path / "task-0001"
    task_dir.mkdir()
    save_task_record(task_dir, _task_record())
    load = load_task_record(task_dir)
    assert load.warning is None
    assert load.record.task_id == "task-0001"
    assert load.record.worktree_id is None
    assert load.record.agent_id is None


def test_missing_task_json_reports_warning(tmp_path):
    load = load_task_record(tmp_path / "nope")
    assert load.record is None
    assert load.warning is not None


def test_malformed_task_json_reports_warning(tmp_path):
    task_dir = tmp_path / "task-0001"
    task_dir.mkdir()
    (task_dir / "TASK.json").write_text("{ not valid", encoding="utf-8")
    load = load_task_record(task_dir)
    assert load.record is None
    assert load.warning is not None


# --- VALIDATION.json -------------------------------------------------------


def test_default_validation_record():
    rec = default_validation_record("task-0001", "2026-01-01T00:00:00Z")
    assert rec.status == VALIDATION_STATUS_NOT_RUN
    assert rec.required_checks == []
    assert rec.approval_reference is None


def test_validation_record_round_trips(tmp_path):
    task_dir = tmp_path / "task-0001"
    task_dir.mkdir()
    rec = ValidationRecord(task_id="task-0001", updated_at="2026-01-01T00:00:00Z", status="passed", test_summary="1 passed")
    save_validation_record(task_dir, rec)
    load = load_validation_record(task_dir)
    assert load.warning is None
    assert load.record.status == "passed"
    assert load.record.test_summary == "1 passed"


def test_missing_validation_json_reports_warning(tmp_path):
    load = load_validation_record(tmp_path / "nope")
    assert load.record is None
    assert load.warning is not None


def test_task_index_record_default_result_state():
    rec = _index_record()
    assert rec.result_state == RESULT_STATE_PENDING
