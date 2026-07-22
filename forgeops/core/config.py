"""Minimal configuration loading: an optional [tool.forgeops] table in the
target repository's pyproject.toml, layered over stdlib-only defaults. No
third-party dependency is required (uses the stdlib tomllib, Python
3.11+)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "log_dir": "logs",
    "oversized_file_bytes": 5 * 1024 * 1024,
    "secret_scan_max_file_bytes": 2 * 1024 * 1024,
    # Extra glob patterns (relative to repo root, fnmatch syntax) where the
    # forgeops:allow-secret marker is honored, beyond the built-in
    # tests/fixtures/examples zones in forgeops/security/secret_scan.py.
    # Never a wildcard covering application source by default.
    "allow_secret_paths": [],
}


class ConfigError(Exception):
    """Raised when [tool.forgeops] exists but is malformed."""


def load_config(repo_root: Path) -> dict[str, Any]:
    config = dict(DEFAULTS)
    pyproject_path = repo_root / "pyproject.toml"
    if not pyproject_path.is_file():
        return config

    try:
        raw = pyproject_path.read_bytes()
        data = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"pyproject.toml could not be read as TOML: {exc}") from exc

    tool_section = data.get("tool", {})
    if not isinstance(tool_section, dict):
        raise ConfigError("[tool] must be a table")

    forgeops_section = tool_section.get("forgeops", {})
    if not isinstance(forgeops_section, dict):
        raise ConfigError("[tool.forgeops] must be a table")

    for key, value in forgeops_section.items():
        if key not in DEFAULTS:
            continue  # unknown keys are ignored, not fatal - forward-compatible
        if not isinstance(value, type(DEFAULTS[key])):
            raise ConfigError(
                f"[tool.forgeops].{key} must be a {type(DEFAULTS[key]).__name__}, "
                f"got {type(value).__name__}"
            )
        if key == "allow_secret_paths" and not all(isinstance(item, str) for item in value):
            raise ConfigError("[tool.forgeops].allow_secret_paths must be a list of strings")
        config[key] = value

    return config
