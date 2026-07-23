"""The ForgeOps-owned process-ownership registry:
`.agent/runtime/PROCESS_REGISTRY.json`. Generalizes the PID/start-time
tracking pattern proven in TrendForge's `trendforge-launcher-common.ps1`
(see `docs/known-failures.md` "Background services remaining active" -
this was planned from Phase 0's source audit, not invented ad hoc).

A registry record is written only for a process ForgeOps itself is aware
of having some relationship to (currently: written/read by
`forgeops cleanup` for stale-record handling and by tests constructing
synthetic records; no ForgeOps command in this checkpoint launches a
long-running background process itself, so the registry is commonly
empty on a real repository - `forgeops process-list`'s discovery does
not depend on it being populated, see `forgeops/detectors/processes.py`).

Never stores secrets, environment values, credentials, or full
command-line text - only a bounded, sanitized fingerprint. Writes go
through the shared atomic-write utility."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forgeops.state.atomic_write import atomic_write_text

REGISTRY_SCHEMA_VERSION = 1
SUPPORTED_REGISTRY_SCHEMA_VERSIONS = {1}
RUNTIME_DIR_RELATIVE = Path(".agent") / "runtime"
REGISTRY_RELATIVE_PATH = RUNTIME_DIR_RELATIVE / "PROCESS_REGISTRY.json"

# Categories a registry record's `category` field is allowed to declare.
# Deliberately excludes anything resembling an editor/shell/browser/VCS/
# agent-CLI category - a record claiming one of those is never trusted
# for cleanup eligibility regardless of any other evidence (defense in
# depth alongside the classification logic in process_association.py).
ALLOWED_MANAGED_CATEGORIES = frozenset({
    "backend-dev-server",
    "frontend-dev-server",
    "test-runner",
    "build-process",
    "forgeops-test-child",
})

NEVER_MANAGED_CATEGORIES = frozenset({
    "browser", "editor", "shell", "git", "claude", "codex", "vcs",
})


@dataclass(frozen=True)
class RegistryRecord:
    pid: int
    category: str
    repository_root: str
    start_time_utc: str | None
    command_fingerprint: str
    creation_source: str
    port: int | None = None
    cleanup_policy: str = "graceful-only"
    created_at_utc: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "category": self.category,
            "repository_root": self.repository_root,
            "start_time_utc": self.start_time_utc,
            "command_fingerprint": self.command_fingerprint,
            "creation_source": self.creation_source,
            "port": self.port,
            "cleanup_policy": self.cleanup_policy,
            "created_at_utc": self.created_at_utc,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "RegistryRecord":
        return RegistryRecord(
            pid=int(data["pid"]),
            category=str(data.get("category", "")),
            repository_root=str(data.get("repository_root", "")),
            start_time_utc=data.get("start_time_utc"),
            command_fingerprint=str(data.get("command_fingerprint", "")),
            creation_source=str(data.get("creation_source", "")),
            port=data.get("port"),
            cleanup_policy=str(data.get("cleanup_policy", "graceful-only")),
            created_at_utc=data.get("created_at_utc"),
        )


@dataclass(frozen=True)
class RegistryDocument:
    schema_version: int = REGISTRY_SCHEMA_VERSION
    records: list[RegistryRecord] = field(default_factory=list)
    warning: str | None = None

    def to_json(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "records": [r.to_dict() for r in self.records],
        }
        return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def load_registry(repo_root: Path) -> RegistryDocument:
    path = repo_root / REGISTRY_RELATIVE_PATH
    if not path.is_file():
        return RegistryDocument(records=[])

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return RegistryDocument(records=[], warning=f"could not read {path.name}: {exc}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return RegistryDocument(records=[], warning=f"{path.name} is not valid JSON: {exc}")

    if not isinstance(data, dict):
        return RegistryDocument(records=[], warning=f"{path.name} top-level value is not an object")

    version = data.get("schema_version")
    if version not in SUPPORTED_REGISTRY_SCHEMA_VERSIONS:
        return RegistryDocument(
            records=[],
            warning=(
                f"{path.name} has schema_version={version!r}, which this version of "
                f"forgeops does not know how to read (supported: "
                f"{sorted(SUPPORTED_REGISTRY_SCHEMA_VERSIONS)}); treated as empty"
            ),
        )

    raw_records = data.get("records", [])
    if not isinstance(raw_records, list):
        return RegistryDocument(records=[], warning=f"{path.name} 'records' is not a list; treated as empty")

    records: list[RegistryRecord] = []
    for entry in raw_records:
        if not isinstance(entry, dict) or "pid" not in entry:
            continue
        try:
            records.append(RegistryRecord.from_dict(entry))
        except (TypeError, ValueError):
            continue

    return RegistryDocument(records=records)


def save_registry(repo_root: Path, document: RegistryDocument) -> Path:
    path = repo_root / REGISTRY_RELATIVE_PATH
    atomic_write_text(path, document.to_json())
    return path
