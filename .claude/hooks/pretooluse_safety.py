#!/usr/bin/env python3
"""PreToolUse safety hook (Claude Code, project-local to ForgeOps).

Fires on Bash / Edit / Write / NotebookEdit tool calls. Fails closed on
malformed input (no decision -> normal permission flow applies, never
crashes, never blocks indefinitely). Deterministic pattern matching only
- no model reasoning, no network access, no credentials read.

Blocks:
1. Any command/file-write whose target path is the read-only reference
   repository (see CLAUDE.md section 6), unless it matches a small
   allow-list of read-only verbs.
2. Destructive git operations anywhere in this repo: `reset --hard`,
   `clean -f`/`-fd`/`-fdx`, force-restoring the working tree
   (`checkout --`/`checkout .`/`restore` without `--staged`), force push.
3. A short list of obviously catastrophic filesystem commands (recursive
   delete of a drive root or home directory, `format`).

Everything else falls through with no decision - normal Claude Code
permission prompting still applies; this hook only ever *adds* a deny,
never grants extra trust."""
from __future__ import annotations

import json
import re
import sys

# Mirrors CLAUDE.md section 6 - the one hardcoded, non-configurable path
# this hook exists to protect. Not read from settings so a malicious or
# accidental settings edit cannot silently disable this specific rule.
TRENDFORGE_PATH_MARKERS = (
    r"C:\Users\joshd\TrendForge",
    "C:/Users/joshd/TrendForge",
)

# Read-only verbs allowed even when they reference the TrendForge path -
# an allow-list (not a deny-list) is deliberately used here: anything not
# on this short list is denied, rather than trying to enumerate every
# possible destructive verb.
_READ_ONLY_BASH_PREFIXES = re.compile(
    r"^\s*(cat|type|head|tail|less|more|ls|dir|find|grep|rg|wc"
    r"|git\s+(?:(?:-C\s+\S+|--no-optional-locks)\s+)*(status|log|diff|show|blame|ls-files))\b",
    re.IGNORECASE,
)

_DESTRUCTIVE_GIT_RE = re.compile(
    r"git\s+(?:\S+\s+)*"
    r"(reset\s+(?:\S+\s+)*--hard"
    r"|clean\s+(?:\S+\s+)*-\S*f\S*"          # -f, -fd, -fdx, ...
    r"|checkout\s+(?:\S+\s+)*--(?:\s|$)"      # git checkout -- <path> (discard)
    r"|checkout\s+\.\s*$"                     # git checkout .
    r"|restore\s+(?!--staged)(?:\S+\s+)*\."   # git restore . (not --staged)
    r"|push\s+(?:\S+\s+)*(?:-f\b|--force\b))",
    re.IGNORECASE,
)

_CATASTROPHIC_FS_RE = re.compile(
    r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+(/|~|\$HOME|/\*|C:\\\\?\s*$)"
    r"|rm\s+-[a-z]*f[a-z]*r[a-z]*\s+(/|~|\$HOME|/\*|C:\\\\?\s*$)"
    r"|rd\s+/s\s+/q\s+[a-zA-Z]:\\\\?\s*$"
    r"|remove-item\s+(?:\S+\s+)*-recurse\s+(?:\S+\s+)*-force\s+(?:\S+\s+)*[a-zA-Z]:\\\\?\s*$"
    r"|format\s+[a-zA-Z]:",
    re.IGNORECASE,
)

_ALLOWED_TRENDFORGE_VERBS = ("cat", "type", "head", "tail", "less", "more", "ls", "dir", "find", "grep", "rg", "wc", "git")


def _mentions_trendforge(text: str) -> bool:
    return any(marker.lower() in text.lower() for marker in TRENDFORGE_PATH_MARKERS)


def _check_bash_command(command: str) -> str | None:
    if not command:
        return None

    if _mentions_trendforge(command) and not _READ_ONLY_BASH_PREFIXES.match(command.strip()):
        return (
            "Blocked by ForgeOps safety hook: command references the read-only "
            "reference repository (TrendForge) and is not a recognized read-only "
            "verb. See CLAUDE.md section 6."
        )

    if _DESTRUCTIVE_GIT_RE.search(command):
        return (
            "Blocked by ForgeOps safety hook: destructive git operation "
            "(reset --hard / clean -f / force checkout-restore / force push) "
            "requires explicit operator approval, not automated execution. "
            "See CLAUDE.md section 7."
        )

    if _CATASTROPHIC_FS_RE.search(command):
        return "Blocked by ForgeOps safety hook: broad destructive filesystem command."

    return None


def _check_file_mutation(file_path: str) -> str | None:
    if file_path and _mentions_trendforge(file_path):
        return (
            "Blocked by ForgeOps safety hook: file write/edit targets the "
            "read-only reference repository (TrendForge). See CLAUDE.md section 6."
        )
    return None


def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        # Fail closed on the *hook itself* crashing, but fail OPEN on the
        # underlying tool call - malformed hook input must never silently
        # grant extra trust, but it also must never hang the session. No
        # decision is emitted, so normal Claude Code permission prompting
        # still governs the tool call.
        print("forgeops-safety-hook: malformed hook input, no decision made", file=sys.stderr)
        return 0

    tool_name = str(data.get("tool_name", ""))
    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    reason: str | None = None
    if tool_name == "Bash":
        reason = _check_bash_command(str(tool_input.get("command", "")))
    elif tool_name in ("Edit", "Write", "NotebookEdit"):
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        reason = _check_file_mutation(path)

    if reason:
        _deny(reason)

    return 0


if __name__ == "__main__":
    sys.exit(main())
