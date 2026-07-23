"""Schema, constants, and atomic load/save for the persistent agent
identity registry: `.agent/agents/AGENT_REGISTRY.json`. Mirrors
`forgeops.state.worktree_registry`'s and `forgeops.state.task_registry`'s
shape and safety properties (atomic writes, schema-version gating,
fail-closed-on-any-bad-record for mutation) - a single flat file, no
per-agent directory or subdirectory, as this checkpoint's brief
explicitly requires. See docs/agents.md for the full contract.

A registered agent is a **declarative identity record only** - `kind`,
`display_name`, `capabilities` (always an empty list this checkpoint;
never inferred or probed), and `assigned_task_id` are the only
meaningful fields beyond bookkeeping. Nothing here ever launches a
process, opens a session, authenticates an account, or stores a
credential, environment value, prompt, or session identifier - see
`forgeops/cli/agent.py` and `docs/agents.md` "Explicit non-goals"."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forgeops.state.atomic_write import atomic_write_text

AGENT_REGISTRY_SCHEMA_VERSION = 1
SUPPORTED_AGENT_REGISTRY_SCHEMA_VERSIONS = {1}

AGENTS_DIR_RELATIVE = Path(".agent") / "agents"
AGENT_REGISTRY_RELATIVE_PATH = AGENTS_DIR_RELATIVE / "AGENT_REGISTRY.json"

# A single safe, lowercase path segment - deliberately rejected rather
# than sanitized on any violation (no automatic ID generation, no
# lowercasing/stripping of an invalid value), mirroring
# `forgeops/worktrees/naming.py:validate_worktree_name` and
# `forgeops/state/task_registry.py:validate_task_id`.
AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MAX_AGENT_ID_LENGTH = 64
MAX_DISPLAY_NAME_LENGTH = 200

KIND_CLAUDE = "claude"
KIND_CODEX = "codex"
KIND_SPECIALIST = "specialist"
KIND_ROCKY = "rocky"
RECOGNIZED_AGENT_KINDS = frozenset({KIND_CLAUDE, KIND_CODEX, KIND_SPECIALIST, KIND_ROCKY})

STATUS_REGISTERED = "registered"
STATUS_DISABLED = "disabled"
RECOGNIZED_AGENT_STATUSES = frozenset({STATUS_REGISTERED, STATUS_DISABLED})


def validate_agent_id(agent_id: str) -> str | None:
    """Return None if `agent_id` is a valid, unambiguous agent identifier,
    or a human-readable rejection reason otherwise. Never mutates
    `agent_id` - an invalid value is rejected outright, never sanitized,
    so a rejected value never silently becomes a different, unintended
    one."""
    if not agent_id or not agent_id.strip():
        return "agent ID must not be empty"
    if Path(agent_id).is_absolute():
        return "agent ID must not be an absolute path"
    if len(agent_id) > MAX_AGENT_ID_LENGTH:
        return f"agent ID must be at most {MAX_AGENT_ID_LENGTH} characters"
    if agent_id != agent_id.lower():
        return "agent ID must be lowercase"
    if not AGENT_ID_RE.match(agent_id):
        return (
            "agent ID must start with a lowercase letter or digit and contain only "
            "lowercase letters, digits, '-', or '_' (rejected rather than sanitized, "
            "to avoid silently changing its meaning)"
        )
    return None


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    kind: str
    display_name: str
    created_at: str
    updated_at: str
    schema_version: int = AGENT_REGISTRY_SCHEMA_VERSION
    status: str = STATUS_REGISTERED
    capabilities: list[str] = field(default_factory=list)
    assigned_task_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "agent_id": self.agent_id,
            "kind": self.kind,
            "display_name": self.display_name,
            "status": self.status,
            "capabilities": list(self.capabilities),
            "assigned_task_id": self.assigned_task_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "AgentRecord":
        return AgentRecord(
            schema_version=int(data.get("schema_version", AGENT_REGISTRY_SCHEMA_VERSION)),
            agent_id=str(data["agent_id"]),
            kind=str(data.get("kind", "")),
            display_name=str(data.get("display_name", "")),
            status=str(data.get("status", STATUS_REGISTERED)),
            capabilities=list(data.get("capabilities") or []),
            assigned_task_id=data.get("assigned_task_id"),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class AgentRegistryDocument:
    schema_version: int = AGENT_REGISTRY_SCHEMA_VERSION
    records: list[AgentRecord] = field(default_factory=list)
    warning: str | None = None

    def to_json(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "records": [r.to_dict() for r in self.records],
        }
        return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def load_agent_registry(repo_root: Path) -> AgentRegistryDocument:
    path = repo_root / AGENT_REGISTRY_RELATIVE_PATH
    if not path.is_file():
        return AgentRegistryDocument(records=[])

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return AgentRegistryDocument(records=[], warning=f"could not read {path.name}: {exc}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return AgentRegistryDocument(records=[], warning=f"{path.name} is not valid JSON: {exc}")

    if not isinstance(data, dict):
        return AgentRegistryDocument(records=[], warning=f"{path.name} top-level value is not an object")

    version = data.get("schema_version")
    if version not in SUPPORTED_AGENT_REGISTRY_SCHEMA_VERSIONS:
        return AgentRegistryDocument(
            records=[],
            warning=(
                f"{path.name} has schema_version={version!r}, which this version of "
                f"forgeops does not know how to read (supported: "
                f"{sorted(SUPPORTED_AGENT_REGISTRY_SCHEMA_VERSIONS)}); treated as malformed"
            ),
        )

    raw_records = data.get("records", [])
    if not isinstance(raw_records, list):
        return AgentRegistryDocument(records=[], warning=f"{path.name} 'records' is not a list; treated as malformed")

    records: list[AgentRecord] = []
    for entry in raw_records:
        if not isinstance(entry, dict) or "agent_id" not in entry:
            return AgentRegistryDocument(records=[], warning=f"{path.name} contains a record missing required field 'agent_id'; treated as malformed")
        try:
            records.append(AgentRecord.from_dict(entry))
        except (TypeError, ValueError) as exc:
            return AgentRegistryDocument(records=[], warning=f"{path.name} contains an unreadable record: {exc}; treated as malformed")

    return AgentRegistryDocument(schema_version=version, records=records)


def save_agent_registry(repo_root: Path, document: AgentRegistryDocument) -> Path:
    path = repo_root / AGENT_REGISTRY_RELATIVE_PATH
    atomic_write_text(path, document.to_json())
    return path
