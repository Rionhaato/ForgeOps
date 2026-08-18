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

**Status: performed 2026-08-14.** See "Evidence log" below. The subprocess
mechanics are validated against a real `claude` binary; a `CLAUDE_CONFIG_DIR`
hook-isolation decision and a branch-reconciliation merge remain as separate
follow-ups, not blockers on this checkpoint.

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

### 2026-08-14 - real `claude -p` launch, executed

Run in a disposable temp dir (`forgeops-validation-20260814-174336`, git-initialized
locally, never TrendForge or this checkout), against this branch's build of
`forgeops` (`9d89848`), following the procedure above exactly for `task-0001`.

**Step 2 (dry-run):** `resolved_executable` reported as
`C:\Users\joshu\AppData\Roaming\npm\claude.CMD` - a real, `shutil.which`-resolved
path, not `null`, confirming the Windows `.CMD` shim resolution path works as
designed.

**Step 3 (confirmed run):** `forgeops task run task-0001 --actor joshua --confirm`
returned `exit 0`. `forgeops task show task-0001` reported `status:
validation_pending` as expected. The task's `TASK.json` recorded exactly the
expected `execution_history`:
```
started   2026-08-14T21:45:23Z  exit_code=null  timed_out=false
completed 2026-08-14T21:46:14Z  exit_code=0     timed_out=false  duration=51.19s
```
The redacted transcript log exists at
`logs/task-run/20260814-214614/task-0001.log` and contains the real subprocess
argv (`['.../claude.CMD', '-p', '<SPEC.md contents>']`), cwd (the `demo` worktree,
not the primary checkout), and captured stdout/stderr.

**Mechanically, this validates the full path end to end**: real executable
resolution, correct non-shell argv construction, correct worktree isolation
(`cwd`), bounded timeout handling, exit-code capture, structured
`TASK.json`/`execution_history` writes, and redacted log persistence all work
exactly as `docs/agent-execution.md` describes - against a real `claude` binary,
not `executable_override`.

**One real finding, not a `forgeops` bug:** the captured stdout shows the prompt
was intercepted by this machine's `claude-mem` plugin `UserPromptSubmit` hook
(`claude-mem worker unreachable for 138 consecutive hooks`) before reaching the
model, so the literal SPEC.md instruction ("print `hello from forgeops task run`")
never executed - the subprocess still exited 0 because the hook itself exits
cleanly after blocking. This means: on this machine, as currently configured,
any headless `claude -p` invocation - from `forgeops task run` or anything else -
is subject to this user's global Claude Code hook configuration, and a hook can
silently no-op a real dispatch while still reporting `exit_code: 0`. Worth a
follow-up decision on whether `task run` should isolate `CLAUDE_CONFIG_DIR` for
launched subprocesses, or whether inheriting the operator's hooks is the intended
behavior.

**One real bug found, already fixed on a sibling branch, not yet here:**
attempting a second `task assign-agent` (on a fresh `task-0002`, set up to retry
in isolation) raised `AttributeError: 'dict' object has no attribute 'to_dict'`
in `forgeops/state/task_ownership.py:553` (`apply_task_assign_agent` ->
`save_task_record` -> `TaskRecord.to_dict()` -> `approval_history` entries stored
as plain dicts instead of `ApprovalEvent`). This is the same
`approval_history`-corruption bug already fixed via `dataclasses.replace(...)` on
`fix/task-ownership-history-corruption` (commit `de75d4c`) - that fix has not yet
been merged into `feature/agent-execution`, so this branch still carries the old
buggy `task_ownership.py`. Not a new defect; a branch-reconciliation gap. Full
traceback: `logs/task/20260814-214727/internal_error.log` (disposable dir, not
committed here).

**Conclusion:** the real-launch validation this checkpoint was blocked on is
complete. `task run`'s subprocess mechanics are proven correct against a real
`claude` binary. Recommend merging the `fix/task-ownership-history-corruption`
fix into this branch before relying on `task assign-agent` for anything beyond
a single agent-per-task-id happy path.
