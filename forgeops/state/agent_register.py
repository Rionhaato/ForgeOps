"""Deterministic core logic behind `forgeops agent register` (see
`forgeops/cli/agent.py`): a read-only preflight-plan builder
(`build_agent_register_plan`) and the single mutating apply step
(`apply_agent_register`), following the same split every other
mutating ForgeOps command uses. Unlike `forgeops task create`/`forgeops
worktree create`, a single JSON file is the entire managed artifact -
no per-agent directory, no secondary files - so apply is a single
atomic write, never a multi-file transaction."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from forgeops.core.paths import is_protected_reference_path
from forgeops.core.timestamps import Clock, iso_now
from forgeops.security.secret_scan import scan_text
from forgeops.state.agent_registry import (
    AGENT_REGISTRY_RELATIVE_PATH,
    MAX_DISPLAY_NAME_LENGTH,
    RECOGNIZED_AGENT_KINDS,
    STATUS_REGISTERED,
    AgentRecord,
    AgentRegistryDocument,
    load_agent_registry,
    save_agent_registry,
    validate_agent_id,
)
from forgeops.state.schema import check_current_state
from forgeops.state.worktree_create import ConflictItem


def _repo_level_conflicts(repo_root: Path) -> list[ConflictItem]:
    conflicts: list[ConflictItem] = []
    if is_protected_reference_path(repo_root):
        conflicts.append(ConflictItem(
            "protected-reference-repo",
            f"{repo_root} is (or is beneath) the configured read-only reference repository - "
            "forgeops agent register will never operate on it",
        ))
    state_check = check_current_state(repo_root / ".agent" / "CURRENT_STATE.json")
    if not state_check.valid:
        conflicts.append(ConflictItem(
            "not-initialized",
            "this project is not an initialized ForgeOps project (.agent/CURRENT_STATE.json is missing or "
            "invalid) - run `forgeops init` first",
        ))
    return conflicts


@dataclass(frozen=True)
class AgentRegisterPlan:
    repo_root: Path
    agent_id: str
    kind: str
    display_name: str
    conflicts: tuple[ConflictItem, ...] = field(default_factory=tuple)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


def build_agent_register_plan(
    repo_root: Path,
    agent_id: str,
    kind: str,
    display_name: str | None,
) -> AgentRegisterPlan:
    """Read-only preflight: classify every possible conflict before any
    mutation is attempted. Never writes anything, never probes an
    installed CLI, never inspects the local machine."""
    conflicts: list[ConflictItem] = _repo_level_conflicts(repo_root)

    id_error = validate_agent_id(agent_id)
    if id_error is not None:
        conflicts.append(ConflictItem("invalid-agent-id", id_error))
    else:
        findings, _ = scan_text(agent_id, relative_path=".agent/agents/__pending_id__")
        if findings:
            categories = ", ".join(sorted({f.category for f in findings}))
            conflicts.append(ConflictItem("agent-id-secret-detected", f"agent ID appears to contain secret-shaped content ({categories}) - refusing to persist it"))

    if kind not in RECOGNIZED_AGENT_KINDS:
        conflicts.append(ConflictItem("invalid-kind", f"kind '{kind}' is not recognized (must be one of {sorted(RECOGNIZED_AGENT_KINDS)})"))

    resolved_display_name = display_name if display_name is not None else agent_id
    if len(resolved_display_name) > MAX_DISPLAY_NAME_LENGTH:
        conflicts.append(ConflictItem("invalid-display-name", f"display name must be at most {MAX_DISPLAY_NAME_LENGTH} characters"))
    elif "\n" in resolved_display_name or "\r" in resolved_display_name:
        conflicts.append(ConflictItem("invalid-display-name", "display name must not contain newlines"))
    else:
        findings, _ = scan_text(resolved_display_name, relative_path=".agent/agents/__pending_display_name__")
        if findings:
            categories = ", ".join(sorted({f.category for f in findings}))
            conflicts.append(ConflictItem("display-name-secret-detected", f"display name appears to contain secret-shaped content ({categories}) - refusing to persist it"))

    registry = load_agent_registry(repo_root)
    if registry.warning is not None:
        conflicts.append(ConflictItem(
            "agent-registry-malformed", f"{AGENT_REGISTRY_RELATIVE_PATH} could not be read safely, refusing to register: {registry.warning}",
        ))
    elif id_error is None and any(r.agent_id == agent_id for r in registry.records):
        conflicts.append(ConflictItem("duplicate-agent-id", f"agent '{agent_id}' is already registered"))

    return AgentRegisterPlan(
        repo_root=repo_root, agent_id=agent_id, kind=kind, display_name=resolved_display_name,
        conflicts=tuple(conflicts),
    )


@dataclass(frozen=True)
class AgentRegisterOutcome:
    ok: bool
    agent_id: str | None
    partial_state: dict | None


def apply_agent_register(repo_root: Path, plan: AgentRegisterPlan, clock: Clock | None = None) -> AgentRegisterOutcome:
    """Execute the single mutating step this checkpoint performs: append
    one declarative identity record to `.agent/agents/AGENT_REGISTRY.json`.
    A single atomic write - never checks for an installed CLI, never
    authenticates, never launches a process. Callers must have already
    verified `plan.has_conflict` is False."""
    fresh_plan = build_agent_register_plan(repo_root, plan.agent_id, plan.kind, plan.display_name)
    if fresh_plan.has_conflict:
        return AgentRegisterOutcome(
            ok=False, agent_id=None,
            partial_state={
                "reason": "registry state changed between preflight and apply - refusing to proceed",
                "conflicts": [{"key": c.key, "message": c.message} for c in fresh_plan.conflicts],
            },
        )

    now = iso_now(clock)
    record = AgentRecord(
        agent_id=plan.agent_id,
        kind=plan.kind,
        display_name=plan.display_name,
        status=STATUS_REGISTERED,
        capabilities=[],
        assigned_task_id=None,
        created_at=now,
        updated_at=now,
        metadata={},
    )

    fresh_registry = load_agent_registry(repo_root)
    if fresh_registry.warning is not None:
        return AgentRegisterOutcome(ok=False, agent_id=None, partial_state={"error": fresh_registry.warning})

    try:
        save_agent_registry(repo_root, AgentRegistryDocument(
            schema_version=fresh_registry.schema_version, records=[*fresh_registry.records, record],
        ))
    except OSError as exc:
        return AgentRegisterOutcome(ok=False, agent_id=None, partial_state={"error": str(exc)})

    return AgentRegisterOutcome(ok=True, agent_id=plan.agent_id, partial_state=None)
