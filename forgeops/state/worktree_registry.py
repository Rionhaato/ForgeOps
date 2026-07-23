"""The ForgeOps-owned worktree registry: `.agent/runtime/WORKTREE_REGISTRY.json`.
Mirrors `forgeops.state.runtime_registry`'s shape and safety properties
(atomic writes, schema-version gating, fail-safe-to-empty-with-a-warning
on anything malformed) for the same reasons: this is bookkeeping
ForgeOps itself owns, not something a user is expected to hand-edit.

Only a `forgeops worktree create` this checkpoint implements ever writes
a record; task/agent ownership fields exist in the schema so a later
checkpoint can populate them without another schema migration, but they
are always written as null for now - see docs/worktrees.md.

Never stores credentials, environment values, or command-line text."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forgeops.state.atomic_write import atomic_write_text

REGISTRY_SCHEMA_VERSION = 1
SUPPORTED_REGISTRY_SCHEMA_VERSIONS = {1}
RUNTIME_DIR_RELATIVE = Path(".agent") / "runtime"
REGISTRY_RELATIVE_PATH = RUNTIME_DIR_RELATIVE / "WORKTREE_REGISTRY.json"

# The only status this checkpoint ever writes. Additional statuses
# (e.g. "removed") belong to a future worktree-remove checkpoint.
STATUS_ACTIVE = "active"


@dataclass(frozen=True)
class WorktreeRecord:
    id: str
    name: str
    path: str
    branch: str
    base_commit: str
    created_at: str
    status: str = STATUS_ACTIVE
    task_id: str | None = None
    agent_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "branch": self.branch,
            "base_commit": self.base_commit,
            "created_at": self.created_at,
            "status": self.status,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "WorktreeRecord":
        return WorktreeRecord(
            id=str(data["id"]),
            name=str(data.get("name", "")),
            path=str(data.get("path", "")),
            branch=str(data.get("branch", "")),
            base_commit=str(data.get("base_commit", "")),
            created_at=str(data.get("created_at", "")),
            status=str(data.get("status", STATUS_ACTIVE)),
            task_id=data.get("task_id"),
            agent_id=data.get("agent_id"),
        )


@dataclass(frozen=True)
class WorktreeRegistryDocument:
    schema_version: int = REGISTRY_SCHEMA_VERSION
    records: list[WorktreeRecord] = field(default_factory=list)
    warning: str | None = None

    def to_json(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "records": [r.to_dict() for r in self.records],
        }
        return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def load_registry(repo_root: Path) -> WorktreeRegistryDocument:
    path = repo_root / REGISTRY_RELATIVE_PATH
    if not path.is_file():
        return WorktreeRegistryDocument(records=[])

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return WorktreeRegistryDocument(records=[], warning=f"could not read {path.name}: {exc}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return WorktreeRegistryDocument(records=[], warning=f"{path.name} is not valid JSON: {exc}")

    if not isinstance(data, dict):
        return WorktreeRegistryDocument(records=[], warning=f"{path.name} top-level value is not an object")

    version = data.get("schema_version")
    if version not in SUPPORTED_REGISTRY_SCHEMA_VERSIONS:
        return WorktreeRegistryDocument(
            records=[],
            warning=(
                f"{path.name} has schema_version={version!r}, which this version of "
                f"forgeops does not know how to read (supported: "
                f"{sorted(SUPPORTED_REGISTRY_SCHEMA_VERSIONS)}); treated as malformed"
            ),
        )

    raw_records = data.get("records", [])
    if not isinstance(raw_records, list):
        return WorktreeRegistryDocument(records=[], warning=f"{path.name} 'records' is not a list; treated as malformed")

    records: list[WorktreeRecord] = []
    for entry in raw_records:
        if not isinstance(entry, dict) or "id" not in entry:
            return WorktreeRegistryDocument(records=[], warning=f"{path.name} contains a record missing required field 'id'; treated as malformed")
        try:
            records.append(WorktreeRecord.from_dict(entry))
        except (TypeError, ValueError) as exc:
            return WorktreeRegistryDocument(records=[], warning=f"{path.name} contains an unreadable record: {exc}; treated as malformed")

    return WorktreeRegistryDocument(records=records)


def save_registry(repo_root: Path, document: WorktreeRegistryDocument) -> Path:
    path = repo_root / REGISTRY_RELATIVE_PATH
    atomic_write_text(path, document.to_json())
    return path
