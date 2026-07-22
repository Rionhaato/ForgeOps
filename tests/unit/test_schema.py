from __future__ import annotations

import json
from pathlib import Path

from forgeops.state.schema import check_current_state


def test_missing_file(tmp_path: Path):
    result = check_current_state(tmp_path / "CURRENT_STATE.json")
    assert result.exists is False
    assert result.valid is False


def test_valid_file(tmp_path: Path):
    path = tmp_path / "CURRENT_STATE.json"
    path.write_text(json.dumps({
        "schema_version": 1, "branch": "main", "mission": "x",
        "completed_work": [], "blockers": [], "next_action": "y",
    }), encoding="utf-8")
    result = check_current_state(path)
    assert result.valid is True
    assert result.missing_keys == []


def test_invalid_json(tmp_path: Path):
    path = tmp_path / "CURRENT_STATE.json"
    path.write_text("{not valid json", encoding="utf-8")
    result = check_current_state(path)
    assert result.exists is True
    assert result.valid_json is False
    assert result.valid is False
    assert result.error is not None


def test_missing_required_keys(tmp_path: Path):
    path = tmp_path / "CURRENT_STATE.json"
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    result = check_current_state(path)
    assert result.valid_json is True
    assert result.valid is False
    assert "branch" in result.missing_keys


def test_non_object_top_level(tmp_path: Path):
    path = tmp_path / "CURRENT_STATE.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    result = check_current_state(path)
    assert result.valid_json is True
    assert result.valid is False
    assert result.error is not None
