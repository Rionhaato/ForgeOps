from __future__ import annotations

from pathlib import Path

import pytest

from forgeops.core.config import ConfigError, DEFAULTS, load_config


def test_defaults_when_no_pyproject(tmp_path: Path):
    config = load_config(tmp_path)
    assert config == DEFAULTS


def test_defaults_when_pyproject_has_no_forgeops_section(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    config = load_config(tmp_path)
    assert config == DEFAULTS


def test_overrides_from_tool_forgeops_section(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.forgeops]\noversized_file_bytes = 1000\n", encoding="utf-8"
    )
    config = load_config(tmp_path)
    assert config["oversized_file_bytes"] == 1000
    assert config["log_dir"] == DEFAULTS["log_dir"]


def test_unknown_keys_are_ignored(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.forgeops]\nsome_future_key = 'x'\n", encoding="utf-8"
    )
    config = load_config(tmp_path)
    assert "some_future_key" not in config


def test_invalid_toml_raises_config_error(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("not [ valid toml", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_wrong_type_raises_config_error(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.forgeops]\nlog_dir = 123\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_forgeops_section_wrong_type_raises(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool]\nforgeops = 'not-a-table'\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_config(tmp_path)
