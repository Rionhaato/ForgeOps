#!/usr/bin/env python3
"""PostToolUse targeted-test hook (Claude Code, project-local to ForgeOps).

Fires on Edit / Write. Deterministic dispatch only - no model reasoning.
Dogfoods ForgeOps' own `forgeops test --targeted` (changed-file-aware
test selection) instead of leaving it to be invoked manually after every
edit, or skipped.

Scope: only files under `forgeops/` or `tests/` ending in `.py`.
Anything else (docs, `.agent/*`, this hook's own script) is a silent
no-op - editing documentation should not trigger a test run.

IMPORTANT, discovered by actually running this before trusting it:
`forgeops test --targeted` plans against the *entire* current git diff,
not just the one file this hook fired for - so if anything else dirty
in the working tree lacks a direct test-file pairing (a new doc, a new
script with no test yet), ForgeOps' own planner deliberately broadens
`scope` to `broad` and the "targeted" run becomes the full ~1381-test
suite. That is correct, conservative behavior on ForgeOps' side, but it
is far too slow to run synchronously inside a PostToolUse hook. This
script therefore always plans first (`--plan --json`, cheap, read-only,
no test execution) and only actually runs tests when the plan comes
back `scope: targeted` - a `broad`/`mixed`/fallback plan is reported,
not executed. (ForgeOps' own planner - forgeops/testing/planner.py -
only ever emits "none" | "targeted" | "broad" | "mixed"; an earlier
version of this hook checked for a `scope: "narrow"` value that does
not exist in the planner's vocabulary, which made the real-execution
and block-on-failure branches below unreachable dead code until this
was caught by live-firing the hook and cross-checking against the
planner source, not by reading this script alone.) This means the hook
goes quiet (plan-only, no test run)
whenever the working tree has other unpaired changes sitting in it,
which is expected, not a bug.

Hard constraints, enforced here rather than assumed:
- never blocks the edit that already happened - PostToolUse cannot undo
  a completed tool call anyway, so this only ever *reports*, it never
  denies
- bounded execution time on every subprocess call, independent of this
  hook's own settings.json timeout
- fails safely on any error (malformed input, missing `forgeops`,
  timeout) - prints a clear message and exits 0, never crashes, never
  hangs the session
- never runs the full suite itself - if planning reports `scope: broad`,
  this hook reports that and stops, it does not execute the fallback
- never commits, pushes, or mutates anything - read-only from the
  repository's point of view"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLAN_TIMEOUT_SECONDS = 15
RUN_TIMEOUT_SECONDS = 45


def _in_scope(cwd: str, file_path: str) -> bool:
    if not cwd or not file_path:
        return False
    if not file_path.endswith(".py"):
        return False
    try:
        rel = Path(file_path).resolve().relative_to(Path(cwd).resolve())
    except (OSError, ValueError):
        return False
    parts = rel.parts
    return bool(parts) and parts[0] in ("forgeops", "tests")


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        print("forgeops-targeted-test-hook: malformed hook input, skipping", file=sys.stderr)
        return 0

    tool_name = str(data.get("tool_name", ""))
    if tool_name not in ("Edit", "Write"):
        return 0

    cwd = str(data.get("cwd") or "")
    tool_input = data.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    file_path = str(tool_input.get("file_path") or "")

    if not _in_scope(cwd, file_path):
        return 0

    try:
        plan_proc = subprocess.run(
            [sys.executable, "-m", "forgeops", "test", "--targeted", "--plan", "--json", "--repo", cwd],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=PLAN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(f"forgeops-targeted-test-hook: plan timed out after {PLAN_TIMEOUT_SECONDS}s", file=sys.stderr)
        return 0
    except OSError as exc:
        print(f"forgeops-targeted-test-hook: could not invoke forgeops: {exc}", file=sys.stderr)
        return 0

    try:
        plan = json.loads(plan_proc.stdout).get("data", {}).get("plan", {})
    except json.JSONDecodeError:
        print("forgeops-targeted-test-hook: could not parse plan output, skipping", file=sys.stderr)
        return 0

    if plan.get("scope") != "targeted" or plan.get("fallback"):
        print(
            "forgeops-targeted-test-hook: plan broadened to scope="
            f"{plan.get('scope')} (fallback={plan.get('fallback')}) - too slow to run "
            "automatically, skipping. Run `forgeops test --targeted` yourself when ready.",
            file=sys.stderr,
        )
        return 0

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "forgeops", "test", "--targeted", "--repo", cwd],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=RUN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print(
            f"forgeops-targeted-test-hook: test --targeted timed out after "
            f"{RUN_TIMEOUT_SECONDS}s for {file_path} (plan had reported scope=targeted)",
            file=sys.stderr,
        )
        return 0
    except OSError as exc:
        print(f"forgeops-targeted-test-hook: could not invoke forgeops: {exc}", file=sys.stderr)
        return 0

    if proc.returncode != 0:
        summary = proc.stdout.strip().splitlines()
        tail = "\n".join(summary[-15:]) if summary else "(no stdout)"
        # Reported both ways deliberately: "decision"/"reason" is the
        # documented PostToolUse mechanism for surfacing feedback to the
        # model, but stderr is kept as a fallback in case that schema
        # isn't honored - this hook must never depend on exactly one
        # channel to be seen.
        print(json.dumps({
            "decision": "block",
            "reason": (
                f"forgeops test --targeted failed (exit {proc.returncode}) after editing "
                f"{file_path}. Last output:\n{tail}"
            ),
        }))
        print(
            f"forgeops-targeted-test-hook: test --targeted failed (exit {proc.returncode}) "
            f"for {file_path}",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
