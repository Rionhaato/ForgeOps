from __future__ import annotations

import json
from pathlib import Path

from forgeops.state.schema import SUPPORTED_SCHEMA_VERSIONS, check_current_state, is_supported_schema_version


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


def test_is_supported_schema_version_known_version():
    assert is_supported_schema_version(1) is True


def test_is_supported_schema_version_unknown_version():
    assert is_supported_schema_version(999) is False


def test_is_supported_schema_version_missing_or_wrong_type():
    assert is_supported_schema_version(None) is False
    assert is_supported_schema_version("1") is False


def test_supported_schema_versions_is_a_set_of_ints():
    assert SUPPORTED_SCHEMA_VERSIONS == {1}
