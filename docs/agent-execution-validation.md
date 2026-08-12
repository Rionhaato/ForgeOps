# Agent Execution: real-launch validation (manual, not automated)

Everything in `docs/agent-execution.md` is covered by
`tests/unit/test_task_execution.py` and
`tests/integration/test_cli_task_run.py` using
`executable_override` - a deterministic fake process, never the real
`claude`/`codex` CLI. That's deliberate (see "Testing without a real
`claude`/`codex` CLI" in `docs/agent-execution.md`), but it means the
actual resolved-executable path (`shutil.which("claude")` -> a real
`claude -p "<prompt>"` subprocess, run against a real worktree) has
never been exercised for real. This is the one validation step this
checkpoint's own test suite cannot cover by design - it must be run by
hand, once, supervised, exactly like Phase 8's Codex adapter is
planned to be validated against a real `codex` executable separately
from its mock-based tests.

**Status: not yet performed.** This is the last outstanding item before
the Agent Execution Foundation checkpoint is considered fully done.

## Procedure

1. In a disposable temporary directory (never TrendForge, never this
   ForgeOps checkout itself unless deliberately dogfooding on a
   throwaway branch):
   ```
   forgeops init
   forgeops task create "Say hello" --spec-file <a short, harmless SPEC.md>
   forgeops worktree create demo
   forgeops task assign task-0001 demo
   forgeops agent register claude-primary --kind claude
   forgeops task assign-agent task-0001 claude-primary
   forgeops task request-approval task-0001 --actor joshua
   forgeops task approve task-0001 --actor joshua --confirm
   ```
2. `forgeops task run task-0001 --actor joshua --dry-run` - confirm the
   reported `resolved_executable` is a real path to `claude`, not
   `null`.
3. `forgeops task run task-0001 --actor joshua --confirm` - watch it
   run live. Confirm:
   - The process actually starts (visible activity, not an instant
     `agent-executable-not-found`).
   - It completes within a reasonable time for a trivial prompt.
   - `forgeops task show task-0001` reports `status: validation_pending`
     and an `execution_history` with `started` then `completed`,
     `exit_code: 0`.
   - `logs/task-run/<timestamp>/task-0001.log` exists and contains the
     real (redacted) transcript.
4. Record the outcome as a dated entry appended to this file (never
   edit the procedure above once real evidence exists below it) -
   command run, exit code, whether `status` and `execution_history`
   matched expectations, and the log path.
5. Only after this passes for real should `.agent/HANDOFF.md` describe
   `task run` as validated end-to-end rather than "unit/integration
   tested against a fake executable only."

## Evidence log

(none yet - see "Status" above)
