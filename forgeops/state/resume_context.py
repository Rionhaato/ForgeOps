"""Deterministic construction of the `forgeops resume-context` compact
document body. Purpose: let a brand-new Claude/Codex session resume this
repository's work without rereading `.agent/CURRENT_STATE.json`,
`.agent/HANDOFF.md`, or the repository itself in full - see
docs/context-efficiency.md.

Deliberately does NOT call `forgeops.state.checkpoint.build_checkpoint_data`
- that function triggers a full repository tree scan (`detect_stack`) to
recompute `detected_stack`, which is unnecessary work for a summary whose
whole purpose is to avoid expensive recomputation. Instead this module
reads only the already-computed narrative fields out of the existing
`CURRENT_STATE.json` (via `load_previous_state`, already proven code) and
combines them with cheap, direct git facts.

Every free-form narrative field (blockers, next_action, in-progress file
lists, ...) is truncated to a small, fixed cap before serialization so the
total document has a strict, tested size ceiling
(`RESUME_CONTEXT_MAX_BYTES`) - see `tests/unit/test_resume_context.py`.
Truncation is capacity-first (fixed per-field caps chosen so the worst
case sum stays well under the ceiling), not a runtime shrink-and-retry
loop, so the bound is simple to reason about and to test."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forgeops.core.git import get_branch_state, get_head, get_status
from forgeops.core.governance import APPROVAL_BOUNDARY_CATEGORIES
from forgeops.core.timestamps import Clock, iso_now
from forgeops.security.redact import redact_text
from forgeops.state.checkpoint import load_previous_state

RESUME_CONTEXT_SCHEMA_VERSION = 1

# Strict, tested ceiling on the serialized JSON document size (UTF-8
# bytes, `indent=2`). Chosen so a new session can hold this document in
# context essentially for free rather than needing to reread state files.
RESUME_CONTEXT_MAX_BYTES = 4096

MAX_BLOCKERS = 3
MAX_BLOCKER_CHARS = 120
MAX_FILES_PER_GROUP = 3
MAX_FILE_PATH_CHARS = 70
MAX_DO_NOT_REPEAT = 3
MAX_DO_NOT_REPEAT_CHARS = 100
MAX_NEXT_ACTION_CHARS = 220
MAX_PHASE_CHARS = 160

STOP_BOUNDARY = (
    "Finish only the current atomic operation, then stop. Do not commit, "
    "push, install dependencies, authenticate a service, or begin a new "
    "checkpoint that was not explicitly approved in this conversation. "
    "See CLAUDE.md section 11 and the approval_boundaries listed above."
)

STATE_RELATIVE_PATH = Path(".agent") / "CURRENT_STATE.json"
HANDOFF_RELATIVE_PATH = Path(".agent") / "HANDOFF.md"


def _truncate(text: str, max_chars: int) -> str:
    text = redact_text(text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


@dataclass(frozen=True)
class BoundedList:
    items: list[str]
    total: int

    def to_dict(self) -> dict[str, Any]:
        return {"items": self.items, "total": self.total, "truncated": self.total > len(self.items)}


def _bounded_strings(values: list[str], max_items: int, max_chars: int) -> BoundedList:
    capped = [_truncate(str(v), max_chars) for v in values[:max_items]]
    return BoundedList(items=capped, total=len(values))


def _latest_validation(recent_tests: list[Any]) -> dict[str, Any] | None:
    """Most recent `python -m pytest ...` entry recorded in
    CURRENT_STATE.json's `recent_tests`, if any - the "latest validated
    test count" resume-context reports rather than re-deriving by
    actually running the suite again."""
    for entry in reversed(recent_tests):
        if not isinstance(entry, dict):
            continue
        command = str(entry.get("command", ""))
        if "pytest" in command:
            return {
                "command": _truncate(command, MAX_DO_NOT_REPEAT_CHARS),
                "result": _truncate(str(entry.get("result", "?")), MAX_DO_NOT_REPEAT_CHARS),
                "timestamp_local": str(entry.get("timestamp_local", "?")),
            }
    return None


def _do_not_repeat(recent_tests: list[Any]) -> list[str]:
    """Commands already validated at the last recorded checkpoint - listed
    so a new session doesn't burn context/time re-running them without a
    code change since. Deduplicated, most-recent occurrence wins, newest
    first."""
    seen: dict[str, str] = {}
    for entry in recent_tests:
        if not isinstance(entry, dict):
            continue
        command = str(entry.get("command", "")).strip()
        if not command:
            continue
        result = str(entry.get("result", "?"))
        seen[command] = f"{command} -> {result}"
    ordered = list(seen.values())[-MAX_DO_NOT_REPEAT:]
    ordered.reverse()
    return [_truncate(item, MAX_DO_NOT_REPEAT_CHARS) for item in ordered]


def build_resume_context(repo_root: Path, clock: Clock | None = None) -> dict[str, Any]:
    """Pure (aside from read-only git/filesystem calls) construction of
    the compact resume-context document. Never writes anything."""
    branch_state = get_branch_state(repo_root)
    head = get_head(repo_root)
    status = get_status(repo_root)

    state_path = repo_root / STATE_RELATIVE_PATH
    handoff_path = repo_root / HANDOFF_RELATIVE_PATH
    previous = load_previous_state(state_path)
    narrative = previous.narrative

    last_checkpoint = narrative.get("last_checkpoint") or {}
    phase = last_checkpoint.get("phase") if isinstance(last_checkpoint, dict) else None

    blockers_raw = narrative.get("blockers") or []
    blockers_bounded = [
        {
            "description": _truncate(str(b.get("description", "?")), MAX_BLOCKER_CHARS),
            "severity": str(b.get("severity", "?")),
        }
        for b in blockers_raw[:MAX_BLOCKERS]
        if isinstance(b, dict)
    ]

    repository = narrative.get("repository") or {}

    document: dict[str, Any] = {
        "schema_version": RESUME_CONTEXT_SCHEMA_VERSION,
        "generated_at": iso_now(clock),
        "repository": {
            "name": repository.get("name"),
            "root": str(repo_root),
        },
        "branch": branch_state.branch,
        "head": head,
        "working_tree": {
            "clean": status.clean,
            "staged": len(status.staged),
            "modified": len(status.modified),
            "untracked": len(status.untracked),
        },
        "phase": _truncate(str(phase), MAX_PHASE_CHARS) if phase else None,
        "latest_validation": _latest_validation(narrative.get("recent_tests") or []),
        "files_in_progress": {
            "modified": _bounded_strings(status.modified, MAX_FILES_PER_GROUP, MAX_FILE_PATH_CHARS).to_dict(),
            "untracked": _bounded_strings(status.untracked, MAX_FILES_PER_GROUP, MAX_FILE_PATH_CHARS).to_dict(),
        },
        "unresolved_blockers": blockers_bounded,
        "unresolved_blockers_total": len(blockers_raw),
        "approval_boundaries": list(APPROVAL_BOUNDARY_CATEGORIES),
        "next_action": _truncate(str(narrative.get("next_action") or ""), MAX_NEXT_ACTION_CHARS) or None,
        "do_not_repeat": _do_not_repeat(narrative.get("recent_tests") or []),
        "stop_boundary": STOP_BOUNDARY,
        "state_paths": {
            "current_state": str(state_path) if state_path.is_file() else None,
            "handoff": str(handoff_path) if handoff_path.is_file() else None,
        },
    }

    if previous.warning:
        document["state_warning"] = _truncate(previous.warning, MAX_BLOCKER_CHARS)

    return document
