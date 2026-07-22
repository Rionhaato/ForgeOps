"""Lightweight, dependency-free validation for .agent/CURRENT_STATE.json.
Full schema-driven state management is Phase 3 work; Phase 2A only needs
enough to answer "does this file exist and is it structurally sane" for
`forgeops status` and `forgeops audit`. See
shared/schemas/current_state.schema.json for the authoritative shape this
mirrors."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REQUIRED_TOP_LEVEL_KEYS = (
    "schema_version",
    "branch",
    "mission",
    "completed_work",
    "blockers",
    "next_action",
)

# The single source of truth for which CURRENT_STATE.json document
# versions this install of forgeops knows how to read/merge. Both the
# lightweight validator below and `forgeops.state.checkpoint` (the
# writer) import this rather than each defining their own supported-set,
# so there is exactly one place to update when the schema evolves.
SUPPORTED_SCHEMA_VERSIONS = {1}


def is_supported_schema_version(version: object) -> bool:
    return version in SUPPORTED_SCHEMA_VERSIONS


@dataclass(frozen=True)
class StateFileCheck:
    exists: bool
    valid_json: bool
    missing_keys: list[str]
    error: str | None

    @property
    def valid(self) -> bool:
        return self.exists and self.valid_json and not self.missing_keys and self.error is None


def check_current_state(path: Path) -> StateFileCheck:
    if not path.is_file():
        return StateFileCheck(exists=False, valid_json=False, missing_keys=[], error=None)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return StateFileCheck(exists=True, valid_json=False, missing_keys=[], error=str(exc))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return StateFileCheck(exists=True, valid_json=False, missing_keys=[], error=str(exc))
    if not isinstance(data, dict):
        return StateFileCheck(exists=True, valid_json=True, missing_keys=[], error="top-level value is not an object")
    missing = [key for key in REQUIRED_TOP_LEVEL_KEYS if key not in data]
    return StateFileCheck(exists=True, valid_json=True, missing_keys=missing, error=None)
