"""Deterministic core logic behind `forgeops init` (forgeops/cli/init.py):
ownership/conflict classification over the minimum managed ForgeOps
project structure, the content each managed path gets when freshly
created, and an all-or-nothing writer with rollback on failure. See
docs/project-init.md for the full ownership model this implements.

Every function here is pure aside from read-only filesystem/git calls -
`build_init_plan` never writes anything, and `apply_init_plan` is the
only function that does, only ever called by `run_init` after preflight
found zero conflicts."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forgeops.core.git import get_branch_state
from forgeops.core.timestamps import Clock, iso_now
from forgeops.state.atomic_write import atomic_write_text
from forgeops.state.checkpoint import PreviousStateLoad, build_checkpoint_data
from forgeops.state.schema import is_supported_schema_version

# First line of every markdown file `forgeops init` creates - the
# ownership marker that lets a later `forgeops init` run (or a human)
# tell "ForgeOps created this and it's safe to leave as-is" apart from
# "a user file happens to live at this path, never touch it". Not used
# for .agent/CURRENT_STATE.json, which already has its own compatibility
# signal (schema_version) via forgeops.state.schema/checkpoint.
FORGEOPS_MANAGED_MARKER = "<!-- forgeops:managed schema=1 -->"

CURRENT_STATE_RELATIVE_PATH = Path(".agent") / "CURRENT_STATE.json"
PROJECT_FACTS_RELATIVE_PATH = Path(".agent") / "PROJECT_FACTS.md"
DECISIONS_RELATIVE_PATH = Path(".agent") / "DECISIONS.md"
HANDOFF_RELATIVE_PATH = Path(".agent") / "HANDOFF.md"
CLAUDE_MD_RELATIVE_PATH = Path("CLAUDE.md")

# Only `.agent` itself is pre-created as an empty directory - it doubles
# as a conflict-detection point (a plain file named `.agent` must block
# init) and as the container every managed file lives in. `.agent/runtime/`,
# `.agent/checkpoints/`, `.agent/logs/` are deliberately NOT pre-created:
# nothing in this checkpoint requires them to exist ahead of time (every
# existing writer, e.g. forgeops.state.runtime_registry, already creates
# its own parent directory via atomic_write_text when it first writes),
# and creating empty speculative directories was explicitly out of scope.
MANAGED_DIRS: tuple[Path, ...] = (Path(".agent"),)


@dataclass(frozen=True)
class PathPlan:
    key: str
    relative_path: Path
    kind: str  # "dir" or "file"
    state: str  # "missing" | "compatible" | "conflict"
    detail: str


@dataclass(frozen=True)
class InitPlan:
    target: Path
    is_git_repo: bool
    branch: str | None

    paths: tuple[PathPlan, ...]

    @property
    def has_conflict(self) -> bool:
        return any(p.state == "conflict" for p in self.paths)

    def to_be_created(self) -> list[PathPlan]:
        return [p for p in self.paths if p.state == "missing"]

    def preserved(self) -> list[PathPlan]:
        return [p for p in self.paths if p.state == "compatible"]

    def conflicts(self) -> list[PathPlan]:
        return [p for p in self.paths if p.state == "conflict"]


def _classify_dir(path: Path) -> tuple[str, str]:
    if not path.exists():
        return "missing", "does not exist yet"
    if path.is_dir():
        return "compatible", "already exists as a directory"
    return "conflict", "exists but is not a directory"


def _classify_markdown(path: Path) -> tuple[str, str]:
    if not path.exists():
        return "missing", "does not exist yet"
    if not path.is_file():
        return "conflict", "exists but is not a regular file"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return "conflict", f"could not read existing file: {exc}"
    first_line = text.splitlines()[0].strip() if text.strip() else ""
    if first_line == FORGEOPS_MANAGED_MARKER:
        return "compatible", "already ForgeOps-managed (ownership marker present) - left unchanged"
    return "conflict", "exists and is not ForgeOps-managed (no ownership marker) - manual review required, not overwritten"


def _classify_current_state(path: Path) -> tuple[str, str]:
    if not path.exists():
        return "missing", "does not exist yet"
    if not path.is_file():
        return "conflict", "exists but is not a regular file"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return "conflict", f"could not read existing file: {exc}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return "conflict", f"existing file is not valid JSON: {exc}"
    if not isinstance(data, dict):
        return "conflict", "existing file's top-level value is not an object"
    version = data.get("schema_version")
    if not is_supported_schema_version(version):
        return "conflict", f"existing file has unsupported schema_version={version!r}"
    return "compatible", "already ForgeOps-managed and schema-compatible - left unchanged"


def build_init_plan(target: Path) -> InitPlan:
    """Read-only preflight: classify every managed path under `target` as
    missing / compatible / conflict. Never writes anything. `is_git_repo`
    is a plain `.git` existence check (not a git-executable call) so it
    stays honest even when no git executable is on PATH at all."""
    is_git = (target / ".git").exists()
    # Only shell out to git when a `.git` entry is actually present - a
    # non-Git directory must never require the git executable at all.
    branch = get_branch_state(target).branch if is_git else None

    paths: list[PathPlan] = []
    for rel in MANAGED_DIRS:
        state, detail = _classify_dir(target / rel)
        paths.append(PathPlan(key=str(rel).replace("\\", "/"), relative_path=rel, kind="dir", state=state, detail=detail))

    state, detail = _classify_current_state(target / CURRENT_STATE_RELATIVE_PATH)
    paths.append(PathPlan(key="current_state", relative_path=CURRENT_STATE_RELATIVE_PATH, kind="file", state=state, detail=detail))

    for key, rel in (
        ("project_facts", PROJECT_FACTS_RELATIVE_PATH),
        ("decisions", DECISIONS_RELATIVE_PATH),
        ("handoff", HANDOFF_RELATIVE_PATH),
        ("claude_md", CLAUDE_MD_RELATIVE_PATH),
    ):
        state, detail = _classify_markdown(target / rel)
        paths.append(PathPlan(key=key, relative_path=rel, kind="file", state=state, detail=detail))

    return InitPlan(target=target, is_git_repo=is_git, branch=branch, paths=tuple(paths))


_INIT_NARRATIVE: dict[str, Any] = {
    "repository": {},
    "mission": "Project initialized under ForgeOps governance.",
    "completed_work": ["forgeops init: initial ForgeOps governance structure created"],
    "recent_tests": [],
    "blockers": [],
    "active_agents": [],
    "owned_files": [],
    "active_worktrees": [],
    "pending_approvals": [],
    "background_processes": [],
    "last_checkpoint": {},
    "next_action": "Run `forgeops doctor` and `forgeops checkpoint` to validate the newly initialized project.",
}


def build_initial_current_state(target: Path, clock: Clock | None = None) -> dict[str, Any]:
    """The document body for a freshly created .agent/CURRENT_STATE.json.
    Reuses forgeops.state.checkpoint.build_checkpoint_data (the same
    pure builder `forgeops checkpoint`/`forgeops handoff` use) rather
    than duplicating its git/stack-detection logic - only the initial
    narrative differs from a from-scratch `forgeops checkpoint` run."""
    narrative = dict(_INIT_NARRATIVE)
    narrative["repository"] = {"name": target.name, "root": str(target)}
    previous = PreviousStateLoad(narrative=narrative, warning=None)
    return build_checkpoint_data(target, previous, clock)


def render_project_facts() -> str:
    return (
        f"{FORGEOPS_MANAGED_MARKER}\n\n"
        "# Project Facts\n\n"
        "Durable technical facts about this project that don't change often "
        "(architecture quirks, external-service gotchas, non-obvious "
        "constraints). Edit in place - this is a living reference, not a "
        "history log (see `DECISIONS.md` for that).\n\n"
        "_No facts recorded yet._\n"
    )


def render_decisions(clock: Clock | None = None) -> str:
    return (
        f"{FORGEOPS_MANAGED_MARKER}\n\n"
        "# Decisions\n\n"
        "Durable architectural decisions, append-only. Add a new dated "
        "entry for each decision; never edit or remove a past entry.\n\n"
        f"## {iso_now(clock)[:10]} - Project initialized\n\n"
        "`forgeops init` established the initial ForgeOps governance "
        "structure for this project.\n"
    )


def render_handoff_seed(clock: Clock | None = None) -> str:
    return (
        f"{FORGEOPS_MANAGED_MARKER}\n\n"
        "# Handoff\n\n"
        f"Generated: {iso_now(clock)}\n\n"
        "## Current phase\n\n"
        "Project initialized by `forgeops init`. No session has run "
        "`forgeops checkpoint`/`forgeops handoff` yet.\n\n"
        "## Next recommended action\n\n"
        "Run `forgeops doctor` and `forgeops checkpoint` to establish a "
        "full project-state snapshot.\n"
    )


def render_claude_md(project_name: str) -> str:
    return (
        f"{FORGEOPS_MANAGED_MARKER}\n\n"
        "# CLAUDE.md\n\n"
        f"This project ({project_name}) is governed by ForgeOps. Rules here "
        "are permanent operating boundaries; see `.agent/PROJECT_FACTS.md` "
        "for durable technical facts, `.agent/DECISIONS.md` for "
        "architectural history, and `.agent/HANDOFF.md`/"
        "`.agent/CURRENT_STATE.json` for current state - read those before "
        "re-investigating what they already answer.\n\n"
        "## Rules\n\n"
        "- Preserve working code: do not remove or break functionality "
        "that already works without explicit instruction to do so.\n"
        "- Require validation evidence (tests actually run this session) "
        "before claiming a change works.\n"
        "- Never write credentials or secret values into tracked files, "
        "prompts, logs, or `.agent/` state.\n"
        "- Read-only ForgeOps inspection commands (`forgeops doctor`, "
        "`status`, `audit`, `changed`, `test --plan`/`--dry-run`) never "
        "modify anything.\n"
        "- Mutations stay within this project - never touch a different "
        "repository, including any ForgeOps-designated read-only "
        "reference repository.\n"
        "- Explicit operator approval is required before: destructive "
        "actions, commits, pushes, installing dependencies, "
        "authenticating a service, spending money, deploying, or any "
        "external communication.\n"
        "- See ForgeOps's own commands (`forgeops doctor|status|audit|"
        "checkpoint|handoff|release-check`) for the deterministic detail "
        "behind these rules rather than duplicating it here.\n"
    )


def _render_content_for(key: str, target: Path, clock: Clock | None) -> str:
    if key == "current_state":
        document = build_initial_current_state(target, clock)
        return json.dumps(document, indent=2, sort_keys=False) + "\n"
    if key == "project_facts":
        return render_project_facts()
    if key == "decisions":
        return render_decisions(clock)
    if key == "handoff":
        return render_handoff_seed(clock)
    if key == "claude_md":
        return render_claude_md(target.name)
    raise ValueError(f"unknown managed path key: {key}")  # pragma: no cover - defensive, unreachable via CLI


@dataclass(frozen=True)
class WriteOutcome:
    key: str
    relative_path: Path
    action: str  # "created_dir" | "created_file" | "preserved"
    ok: bool
    detail: str


def apply_init_plan(target: Path, plan: InitPlan, clock: Clock | None = None) -> tuple[list[WriteOutcome], str | None]:
    """Create every 'missing' path in `plan`, in a fixed order
    (directories first, then files), atomically per file. If any single
    write fails, every path this call created earlier in the same run is
    removed again (best-effort, in reverse order) before returning, so a
    failed `forgeops init` never leaves a half-initialized project -
    only pre-existing ('compatible') paths are ever left untouched.
    Returns (outcomes, error_message); error_message is None on full
    success. Callers must have already verified plan.has_conflict is
    False - this function does not re-check conflicts."""
    outcomes: list[WriteOutcome] = []
    created_paths: list[Path] = []

    def _rollback() -> None:
        for p in reversed(created_paths):
            try:
                if p.is_dir():
                    p.rmdir()
                else:
                    p.unlink(missing_ok=True)
            except OSError:
                pass  # best-effort cleanup only; nothing else safe to do here

    for path_plan in plan.paths:
        if path_plan.state != "missing":
            outcomes.append(WriteOutcome(
                key=path_plan.key, relative_path=path_plan.relative_path,
                action="preserved", ok=True, detail=path_plan.detail,
            ))
            continue

        abs_path = target / path_plan.relative_path
        action = "created_dir" if path_plan.kind == "dir" else "created_file"
        try:
            if path_plan.kind == "dir":
                abs_path.mkdir(parents=True, exist_ok=False)
            else:
                content = _render_content_for(path_plan.key, target, clock)
                atomic_write_text(abs_path, content)
            created_paths.append(abs_path)
            outcomes.append(WriteOutcome(path_plan.key, path_plan.relative_path, action, True, "created"))
        except OSError as exc:
            outcomes.append(WriteOutcome(path_plan.key, path_plan.relative_path, action, False, str(exc)))
            _rollback()
            return outcomes, str(exc)

    return outcomes, None
