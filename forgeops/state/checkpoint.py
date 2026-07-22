"""Deterministic construction of the `.agent/CURRENT_STATE.json` document
body. Shared, pure logic behind both `forgeops checkpoint` (writes this
document) and `forgeops handoff` (renders a markdown summary derived from
it) - see docs/checkpoint-and-handoff.md for the consistency model
between the two commands.

`build_checkpoint_data()` never touches the filesystem outside reading
the previous state file it is handed and never invokes model reasoning;
every field is either read verbatim from git/the filesystem or carried
forward unchanged from the previous state document. Narrative fields a
human or agent previously wrote (`mission`, `completed_work`, `blockers`,
`next_action`, `last_checkpoint.phase`) are preserved rather than
reinvented, because no deterministic script can honestly synthesize
"what phase of the mission is this" from git state alone - only the
objectively observable fields (branch, HEAD, working-tree shape,
detected stack, remote presence) are recomputed every call."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forgeops.core.config import load_config
from forgeops.core.git import get_ahead_behind, get_branch_state, get_head, get_remotes, get_status
from forgeops.core.timestamps import Clock, iso_now
from forgeops.detectors.stack import detect_stack
from forgeops.detectors.tree_scan import scan_repo_tree
from forgeops.state.schema import SUPPORTED_SCHEMA_VERSIONS, is_supported_schema_version

CURRENT_STATE_SCHEMA_VERSION = 1
# Re-exported for backward compatibility with callers/tests written
# against this module's own name; forgeops.state.schema is the single
# source of truth (see its module docstring/comment).
SUPPORTED_STATE_SCHEMA_VERSIONS = SUPPORTED_SCHEMA_VERSIONS

# Narrative fields no deterministic recomputation can honestly invent -
# carried forward from the previous state document untouched. Everything
# else in the document is recomputed fresh every call.
_PRESERVED_NARRATIVE_KEYS = (
    "repository",
    "mission",
    "completed_work",
    "recent_tests",
    "blockers",
    "active_agents",
    "owned_files",
    "active_worktrees",
    "pending_approvals",
    "background_processes",
    "last_checkpoint",
    "next_action",
)

_DEFAULT_NARRATIVE: dict[str, Any] = {
    "repository": {},
    "mission": "",
    "completed_work": [],
    "recent_tests": [],
    "blockers": [],
    "active_agents": [],
    "owned_files": [],
    "active_worktrees": [],
    "pending_approvals": [],
    "background_processes": [],
    "last_checkpoint": {},
    "next_action": "",
}


@dataclass(frozen=True)
class PreviousStateLoad:
    """Result of attempting to read a previous `.agent/CURRENT_STATE.json`.
    `narrative` is always usable (falls back to empty defaults);
    `warning` is set whenever the previous file existed but couldn't be
    trusted verbatim (missing, unreadable, invalid JSON, or an
    unsupported/future schema_version) - checkpoint/handoff surface this
    as a `warning`-status check rather than silently discarding data."""

    narrative: dict[str, Any]
    warning: str | None


def load_previous_state(path: Path) -> PreviousStateLoad:
    if not path.is_file():
        return PreviousStateLoad(narrative=dict(_DEFAULT_NARRATIVE), warning=None)

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return PreviousStateLoad(
            narrative=dict(_DEFAULT_NARRATIVE),
            warning=f"could not read existing {path.name}: {exc}",
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return PreviousStateLoad(
            narrative=dict(_DEFAULT_NARRATIVE),
            warning=f"existing {path.name} is not valid JSON: {exc}",
        )

    if not isinstance(data, dict):
        return PreviousStateLoad(
            narrative=dict(_DEFAULT_NARRATIVE),
            warning=f"existing {path.name} top-level value is not an object",
        )

    version = data.get("schema_version")
    if not is_supported_schema_version(version):
        return PreviousStateLoad(
            narrative=dict(_DEFAULT_NARRATIVE),
            warning=(
                f"existing {path.name} has schema_version={version!r}, which this "
                f"version of forgeops does not know how to merge (supported: "
                f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}); narrative fields were "
                "reset to empty defaults rather than guessed at"
            ),
        )

    narrative = dict(_DEFAULT_NARRATIVE)
    for key in _PRESERVED_NARRATIVE_KEYS:
        if key in data:
            narrative[key] = data[key]
    return PreviousStateLoad(narrative=narrative, warning=None)


def _stack_summary(repo_root: Path) -> list[dict[str, Any]]:
    config = load_config(repo_root)
    tree = scan_repo_tree(repo_root, config["oversized_file_bytes"], config["secret_scan_max_file_bytes"])
    findings = detect_stack(repo_root, tree)
    return [
        {
            "technology": f.technology,
            "confidence": f.confidence,
            "ambiguous": f.ambiguous,
            "evidence_count": len(f.evidence),
        }
        for f in findings
    ]


def build_checkpoint_data(
    repo_root: Path,
    previous: PreviousStateLoad,
    clock: Clock | None = None,
) -> dict[str, Any]:
    """Pure (aside from the read-only git/filesystem calls) construction
    of the full CURRENT_STATE.json document body. Never writes anything -
    callers decide whether/where to persist the result."""
    branch_state = get_branch_state(repo_root)
    head = get_head(repo_root)
    remotes = get_remotes(repo_root)
    status = get_status(repo_root)
    ahead_behind = get_ahead_behind(repo_root)

    document: dict[str, Any] = {
        "schema_version": CURRENT_STATE_SCHEMA_VERSION,
        "generated_at": iso_now(clock),
        "branch": branch_state.branch,
        "head": head,
        "remote": {
            "configured": bool(remotes),
            "names": [r.name for r in remotes],
            "upstream": ahead_behind.upstream,
            "ahead": ahead_behind.ahead,
            "behind": ahead_behind.behind,
        },
        "changed_files": {
            "staged": len(status.staged),
            "modified": len(status.modified),
            "untracked": len(status.untracked),
        },
        "has_local_changes": not status.clean,
        "detected_stack": _stack_summary(repo_root),
    }

    for key in _PRESERVED_NARRATIVE_KEYS:
        document[key] = previous.narrative.get(key, _DEFAULT_NARRATIVE.get(key))

    return document
