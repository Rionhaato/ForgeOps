# Agent Execution Foundation (`forgeops task run`)

Launches a real `claude` or `codex` subprocess against an approved,
fully-assigned task, synchronously and within a bounded timeout. This
is the first ForgeOps command that spawns a real external process
rather than only reading or writing JSON - see "Explicit non-goals"
below for what is deliberately still out of scope.

## Where this lives

There is no `forgeops/agents/` package (removed in an earlier
checkpoint - see `docs/agents.md`). The canonical implementation is:

- `forgeops/state/task_execution.py` - `build_task_run_plan`/
  `apply_task_run`, the preflight/apply pair
- `forgeops/cli/task.py` - `run_task_run`, the CLI handler

## Commands

```
forgeops task run TASK_ID --actor ACTOR
forgeops task run TASK_ID --actor ACTOR --timeout SECONDS
forgeops task run TASK_ID --actor ACTOR --dry-run
forgeops task run TASK_ID --actor ACTOR --confirm
forgeops task run TASK_ID --actor ACTOR --confirm --json
```

`--timeout` defaults to 3600 seconds. `--actor` is required on every
invocation, including `--dry-run` - this is the single most
consequential command in ForgeOps (it can cause arbitrary file edits
and shell commands to run), so knowing who authorized a launch matters
even for a preview.

## Precondition: an initialized ForgeOps project

Reuses `forgeops.cli.task._repo_level_block`, the same shared gate
every task command uses: refuses the read-only reference repository
(TrendForge), and requires `.agent/CURRENT_STATE.json` to already exist
and be schema-valid (run `forgeops init` first).

## Full precondition chain

A task can only be run when **all** of the following hold - each
missing piece is its own conflict, not a single combined error, so
`--dry-run` reports exactly what's missing:

| Condition | Conflict key if missing |
|---|---|
| Task exists, is schema-valid, project-root matches | `task-not-found` / `task-json-malformed` / `task-project-root-mismatch` |
| `status` is not terminal | `task-already-terminal` |
| `approval_state == approved` | `task-not-approved` |
| An agent is assigned | `task-no-agent-assigned` |
| The assigned agent is registered, not disabled | `agent-not-found` / `agent-disabled` |
| The assigned agent's `kind` is `claude` or `codex` | `unsupported-agent-kind` |
| The kind's executable is resolvable on PATH | `agent-executable-not-found` |
| A worktree is assigned | `task-no-worktree-assigned` |
| The assigned worktree exists, is active, not removed/locked/stale | `worktree-not-found` / `worktree-removed` / `worktree-locked` / `worktree-stale` |
| `--actor` is valid and not secret-shaped | `invalid-actor` / `actor-secret-detected` |
| `SPEC.md` is readable and not secret-shaped | `spec-unreadable` / `spec-secret-detected` |

**The worktree requirement is a hard block, not a warning** - unlike
`task assign-agent` (where a missing worktree is only advisory, since
agent and worktree ownership are independent fields), `task run`
refuses outright. The entire point of the worktree system
(`docs/worktrees.md`) is isolating agent-driven changes from the
primary checkout; running an agent against the primary checkout would
quietly defeat that, so this command never allows it.

`specialist`/`rocky` agent kinds are recognized identities elsewhere in
ForgeOps (`docs/agents.md`) but have no defined executable yet - a task
assigned to one is blocked by `unsupported-agent-kind`, not silently
skipped.

## What a run does (and does not)

- Prompt: the raw text of the task's `SPEC.md`, unmodified.
- Executable: `claude` for a `claude`-kind agent, `codex` for a
  `codex`-kind agent, resolved via the same `shutil.which()` approach
  `forgeops.core.subprocess_utils.run` already performs internally for
  every other subprocess call in ForgeOps (so a dry-run's
  `agent-executable-not-found` check and the real launch always agree).
- Working directory: the assigned worktree's path - **never** the
  primary checkout.
- Execution is synchronous and bounded by `--timeout` - there is no
  background process, no heartbeat, no polling. Nothing else in
  ForgeOps uses `subprocess.Popen`; `"claude"`/`"codex"` are already
  hard-coded into
  `forgeops.state.runtime_registry.NEVER_MANAGED_CATEGORIES`, so a
  blocking call needs no process-registry entry - `forgeops cleanup`
  was already designed to never touch an agent process regardless.
- MCP: whatever MCP servers are already configured on this machine for
  the launched CLI (e.g. Stitch at user scope) are automatically
  available to it - ForgeOps itself configures none of this.
- `status` transitions to `active` immediately before launch, then to
  exactly one of:
  - `validation_pending` - the process exited 0 and did not time out.
  - `blocked` - the process exited non-zero, or timed out.

  **Never `completed` or `failed`** - those two terminal statuses
  remain `task close`'s exclusive privilege, unchanged by this
  checkpoint. A successful run only makes a task *eligible* for
  validation and closure, never closes it automatically.
- `TASK.json.execution_history` gains exactly two events per attempt
  (`started`, then one of `completed`/`failed`/`timed_out`) - append-
  only, mirroring `approval_history`'s shape and the same "a lookup
  miss means refused, not assumed" philosophy used throughout. Each
  event records `actor`, `timestamp`, `exit_code`, `timed_out`,
  `duration_seconds`, and `log_path`.
- `TASK_INDEX.json.last_execution_status` is a summary copy of the most
  recent event's name - never authoritative, exactly like every other
  index field.

## CLAUDE_CONFIG_DIR isolation for `claude`-kind launches

A launched subprocess inherits the full parent environment by default
(`forgeops.core.subprocess_utils.run`'s `env=None` behavior, unchanged
for every caller except this one) - so without isolation, a `claude`
launch reads the exact same `~/.claude` as the operator's own
interactive session, hooks and all. Real-execution validation on
2026-08-14 (see `docs/agent-execution-validation.md`) found this
causes a genuine correctness bug: a personal hook (claude-mem's
`UserPromptSubmit`) intercepted the launched process's prompt before
the model saw it, and `claude` still exited 0 - so the run was
recorded as `validation_pending` (looks successful) even though
nothing happened.

To prevent this, `apply_task_run` builds a fresh, empty, disposable
directory per run (`tempfile.mkdtemp`) and points a `claude`-kind
launch's `CLAUDE_CONFIG_DIR` at it instead of inheriting the
operator's. The directory is removed again once the subprocess
returns. This is scoped narrowly:

- **Only `claude`-kind launches are affected.** `codex` has no
  equivalent config-dir env var, so `codex`-kind launches still
  inherit the parent environment unchanged.
- **Only hooks/plugins/settings are isolated, not MCP availability.**
  `~/.claude/settings.json` (hooks) lives under `CLAUDE_CONFIG_DIR`,
  but user-scope MCP server registrations live in the sibling
  `~/.claude.json`, resolved via `HOME`/`USERPROFILE` - untouched by
  this change. The "MCP: ... automatically available" behavior above
  still holds; only the operator's personal hook/plugin config is kept
  from silently interfering with an automated launch.
- **Authentication is preserved by copying the on-disk credential.**
  FO-010 real-launch validation (2026-08-18) found that
  `CLAUDE_CONFIG_DIR` is a full replacement for `~/.claude`, not just
  its hooks - the session credential lives at
  `<CLAUDE_CONFIG_DIR>/.credentials.json`, so a bare empty isolated
  directory logged the launched process out entirely (`claude` exited
  1, "Not logged in", instead of ever reaching the model). The fix
  copies just that one file into the isolated directory before launch;
  `settings.json`/hooks/plugins are still deliberately not copied.
  If `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` is set in the
  operator's environment - the documented headless-auth path, which
  needs no credentials file at all - it's already preserved without
  any extra handling, since the isolated launch's environment is a
  copy of the full parent environment with only `CLAUDE_CONFIG_DIR`
  overridden.
- **The credential lookup falls back to `~/.claude` if the active
  `CLAUDE_CONFIG_DIR` doesn't have one.** FO-010 round 2 (2026-08-19)
  found the first version of this fix still failed when
  `CLAUDE_CONFIG_DIR` was already overridden to a location that has
  hooks/settings but no credentials - a real hook installation never
  actually looks like that (hooks and credentials always coexist in
  the real `~/.claude`), but the override could still legitimately
  point somewhere else for other reasons. `_find_real_claude_credentials()`
  now tries the active override first, and if that location has no
  `.credentials.json`, falls back to checking the `~/.claude` default -
  using the first location that actually has one, or copying nothing
  if neither does.
- **Known limitation:** if `claude` refreshes the session credential
  during the run, the refreshed token is written to the isolated
  copy, not the operator's real `.credentials.json`, and is lost when
  the isolated directory is removed afterward. This is a pre-existing
  short-lived-token risk, not something this isolation introduces;
  using `CLAUDE_CODE_OAUTH_TOKEN` instead avoids it entirely.

## Secrets: block going in, redact coming out

The prompt (`SPEC.md`'s content) is scanned with
`forgeops.security.secret_scan.scan_text` before launch - a match
blocks the run outright (`spec-secret-detected`), mirroring how
`task_approval.py` already blocks on a secret-shaped actor/reason.
`--actor` is scanned and validated the same way `task approve` already
validates its own `--actor`.

Captured stdout/stderr, in contrast, is **always** written through the
existing `forgeops.reporting.logs.LogWriter` (which already redacts
everything via `forgeops.security.redact.redact_text` before touching
disk) - refusing to record that a run happened, after the process
already did real work, would make no sense. The raw, redacted log lands
at `logs/task-run/<timestamp>/<task-id>.log`; only a concise summary
(exit code, timed-out flag, log path) ever returns in the command
result itself, per `CLAUDE.md` §6.

## Two separate atomic writes, not one atomic pair

Every other mutating task command in this codebase writes its
authoritative record(s) as a single atomic pair (see
`docs/tasks.md`/`docs/approvals.md`). `task run` cannot do that: the
subprocess call between the "started" write and the final write can
take up to `--timeout` seconds, and holding a rollback window open
across an external process running for up to an hour would be
meaningless. Instead:

1. `TASK.json` is written once with `status=active` and a `started`
   event, **before** the subprocess launches.
2. The subprocess runs.
3. `TASK.json` is written again with the final `status` and a second
   event, **after** the subprocess returns (or times out).
4. `TASK_INDEX.json` is updated last, bookkeeping only - its own
   failure is `WARNINGS_PRESENT`, never a call failure, mirroring
   `task assign`/`task assign-agent`'s established convention. The
   subprocess already ran; a task whose real-world execution succeeded
   is never reported as failed just because the summary index lagged.

If the *first* write fails, nothing has been attempted yet -
`outcome.ok = False`, `COMMAND_EXECUTION_FAILURE`, and the command
reports normally, mirroring every other write-failure path in this
codebase. If the *second* write fails, the subprocess has already run
to completion - the outcome distinguishes this explicitly
(`partial_state.note`), since there is nothing meaningful to "roll
back" to.

## Testing without a real `claude`/`codex` CLI

`apply_task_run(..., executable_override=[...])` replaces the resolved
`claude`/`codex` command entirely. No test in this repository shells
out to a real, costly, non-deterministic agent CLI - unit and
integration tests use a small deterministic
`[sys.executable, "-c", "..."]` script to simulate success, failure,
and timeout. A **real** launch against the real `claude` CLI is
validated once, by hand, together - not part of the automated suite -
exactly mirroring how Phase 8's Codex adapter is already planned
("adapter can be built and tested against a mock; real-executable
testing is a separate scoped step").

## Exit codes

- `REPO_NOT_FOUND` (4) / `COMMAND_EXECUTION_FAILURE` (5, git
  unavailable, or a genuine write failure) as every other command.
- `BLOCKED` (2) - the read-only reference repository; an uninitialized
  project; any preflight conflict from the table above; `--confirm`
  missing on a non-dry-run invocation.
- `WARNINGS_PRESENT` (1) - the write succeeded but the launched process
  exited non-zero or timed out (`status` is now `blocked`); or the
  process succeeded but the `TASK_INDEX.json` update failed.
- `SUCCESS` (0) - a conflict-free `--dry-run`; or a confirmed run where
  the process exited 0 and the index updated cleanly.

## Explicit non-goals (this checkpoint)

No MCP configuration by ForgeOps itself (the launched process inherits
whatever is already configured for that CLI on this machine), no
parallel/concurrent execution of multiple tasks, no automatic retry, no
process monitoring beyond the bounded `--timeout` (no heartbeats, no
progress streaming), no background/detached execution, no automatic
`task close`, no Rocky integration, no ForgeStudio, no execution for
`specialist`/`rocky` agent kinds, no killing an in-flight run (the
timeout is the only bound), no credential/environment-variable
injection beyond what the resolved executable already inherits from
this process's own environment.
