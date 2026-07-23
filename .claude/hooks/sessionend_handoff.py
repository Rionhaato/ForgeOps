#!/usr/bin/env python3
"""SessionEnd handoff hook (Claude Code, project-local to ForgeOps).

Fires once when a session actually ends (clear/resume/logout/exit) - not
after every turn (that is the `Stop` event, deliberately not used here;
see docs/context-efficiency.md for why). Invokes the existing, proven
`forgeops handoff` writer (atomic, deterministic, already covered by its
own test suite) so `.agent/HANDOFF.md` reflects the repository's state at
session end.

Hard constraints, enforced here rather than assumed:
- never runs the test suite
- never commits or pushes (forgeops handoff does neither)
- bounded execution time (subprocess timeout, independent of the hook's
  own settings.json timeout)
- never blocks session shutdown - SessionEnd hooks cannot block anyway,
  but this script also never raises past its own boundary
- fails loudly (clear stderr message) but safely (exit 0 either way) if
  the write itself fails"""
from __future__ import annotations

import json
import subprocess
import sys

SUBPROCESS_TIMEOUT_SECONDS = 25


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        print("forgeops-sessionend-hook: malformed hook input, skipping handoff write", file=sys.stderr)
        return 0

    cwd = str(data.get("cwd") or "")
    if not cwd:
        print("forgeops-sessionend-hook: no cwd in hook input, skipping handoff write", file=sys.stderr)
        return 0

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "forgeops", "handoff", "--repo", cwd],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(f"forgeops-sessionend-hook: handoff write timed out after {SUBPROCESS_TIMEOUT_SECONDS}s", file=sys.stderr)
        return 0
    except OSError as exc:
        print(f"forgeops-sessionend-hook: could not invoke forgeops handoff: {exc}", file=sys.stderr)
        return 0

    if proc.returncode not in (0, 1):  # 0 = clean, 1 = WARNINGS_PRESENT (still a successful write)
        print(
            f"forgeops-sessionend-hook: forgeops handoff exited {proc.returncode}; "
            f"last line: {proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else '(no output)'}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
