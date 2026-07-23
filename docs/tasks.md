# Task Specification Engine (`forgeops task create|show|list|validate|close`)

A persistent, schema-controlled store for task intent, scope, acceptance
criteria, validation expectations, and final outcome - kept outside
conversational context under `.agent/tasks/`. This checkpoint
implements only these five commands - see "Explicit non-goals" below.

## Commands

```
forgeops task create TITLE [--repo PATH]
forgeops task create TITLE [--repo PATH] --spec-file FILE
forgeops task create TITLE [--repo PATH] --acceptance FILE
forgeops task create TITLE [--repo PATH] --dry-run
forgeops task create TITLE [--repo PATH] --json

forgeops task show TASK_ID [--repo PATH]
forgeops task show TASK_ID [--repo PATH] --json

forgeops task list [--repo PATH]
forgeops task list [--repo PATH] --status STATUS
forgeops task list [--repo PATH] --json

forgeops task validate TASK_ID [--repo PATH]
forgeops task validate TASK_ID [--repo PATH] --json

forgeops task close TASK_ID [--repo PATH] --result-file FILE
forgeops task close TASK_ID [--repo PATH] --dry-run
forgeops task close TASK_ID [--repo PATH] --confirm
forgeops task close TASK_ID [--repo PATH] --json
```

`forgeops task` with no subcommand is a plain argparse usage error
(exit 2), matching every other two-level ForgeOps subcommand.

## Precondition: an initialized ForgeOps project

Every task command refuses to run unless `.agent/CURRENT_STATE.json`
exists and is schema-valid (run `forgeops init` first), and refuses the
configured read-only reference repository (TrendForge) outright - see
`forgeops/cli/task.py:_repo_level_block`, the single shared gate all
five commands call before doing anything else.

## Managed task structure

```
.agent/tasks/
    TASK_INDEX.json
    <task-id>/
        TASK.json
        SPEC.md
        VALIDATION.json
        RESULT.md          # only created by `task close`
```

## Task ID generation

Deterministic, filesystem-safe, human-readable: `task-0001`,
`task-0002`, ... - `task-` followed by at least four digits
(`forgeops/state/task_registry.py:TASK_ID_RE`), rejected outright
rather than sanitized on any violation (absolute path, traversal,
separators), the same allow-list philosophy
`forgeops/worktrees/naming.py:validate_worktree_name` established for
worktree names.

The next ID comes from `TASK_INDEX.json`'s own `next_task_number`
counter - never derived from a timestamp, and never reused. It only
advances after a task directory (TASK.json + SPEC.md + VALIDATION.json)
has been fully, successfully written; a failed creation attempt rolls
back whatever it created in that same call, so the number is safely
retried next time. If a previous attempt's directory is somehow left
behind anyway, the next attempt at that same ID hits a
`destination-exists` conflict and refuses rather than overwriting it -
a human must resolve it; nothing is silently reused or repaired
automatically.

## Task creation

`forgeops task create TITLE` is mutating and supports `--dry-run`
(identical preflight, zero mutation, same exit code a real run would
give). A created task starts at status `draft`, `approval_state`
`not_requested`, `worktree_id`/`agent_id` both `null` - this checkpoint
never assigns either.

`TASK.json`'s fields: `schema_version`, `task_id`, `title`, `status`,
`project_root`, `created_at`, `updated_at`, `created_by` (always
`"forgeops-cli"`), `scope_summary` (the title itself, plus a pointer to
SPEC.md - never an inferred implementation plan), `accepted_checkpoint`
/ `source_branch` / `source_head` (captured from git at creation time),
`worktree_id`, `agent_id`, `approval_state`, `blockers`,
`dependencies`, `managed_files` (all empty lists at creation),
`validation_state`, `result_state`.

`SPEC.md` always has exactly these sections: Objective, In Scope, Out
of Scope, Constraints, Acceptance Criteria, Required Validation, Stop
Boundary. The Constraints section deliberately just points at
`CLAUDE.md` and this project's own `.agent/` governance files rather
than copying them - see `forgeops/state/task_spec.py:render_spec_md`.

- **No `--spec-file`**: a minimal template is generated, every section
  present but holding a `(not yet defined...)` placeholder requiring
  later human completion - `task create` never infers a large
  implementation plan from `TITLE`.
- **`--spec-file FILE`**: the file's content becomes `SPEC.md` verbatim
  (after the checks below) - its own structure is trusted as-is, not
  forced into the seven-section template.
- **`--acceptance FILE`**: parsed into a flat list of criteria (a `.json`
  file must be an array of strings, or an object with a `criteria`
  array; anything else is treated as plain text, one non-empty line per
  criterion) and becomes the Acceptance Criteria section - appended as
  `## Acceptance Criteria (imported)` when combined with `--spec-file`
  (a custom spec file's own layout is never rewritten in place),
  otherwise substituted directly into the generated template. Rejected
  outright if it looks like an executable script (a recognized
  script/executable extension, or a `#!` shebang line) - "do not accept
  executable scripts in this checkpoint."

Both `--spec-file` and `--acceptance` are read-only, safe file reads
(`forgeops/state/task_spec.py:read_source_file`): rejected if missing,
a directory, or larger than `[tool.forgeops].secret_scan_max_file_bytes`
(2 MB by default - the same threshold the codebase already uses to
decide "too large to safely secret-scan", reused here since that's
exactly why the limit exists). Content is never executed or
interpreted as commands, and is scanned for secret-shaped content
(`forgeops.security.secret_scan.scan_text`) before ever being
persisted - a match blocks creation outright.

## Task index

`.agent/runtime`-style atomic, schema-versioned document at
`.agent/tasks/TASK_INDEX.json`, mirroring
`forgeops/state/worktree_registry.py`'s shape and safety properties:
any single unreadable record fails the *whole* index closed (mutating
commands refuse outright on a malformed index; `task list`/`task show`
still work, reporting it as a warning, never repairing it
automatically). Holds only concise summaries - task ID, title, status,
timestamps, relative path, worktree/agent assignment, approval state,
validation state, result state - never full specs or results.

## Task show

Read-only. Reuses the same structural-consistency check `task validate`
runs (`forgeops/state/task_validate.py:validate_task`), but never
refuses on anything except a task that genuinely cannot be located
(invalid ID, or no directory and no index entry) - `BLOCKED`, with
`data.found = false`. Any other issue (a placeholder Acceptance
Criteria section, an unresolvable accepted checkpoint, ...) is surfaced
as a warning check (`WARNINGS_PRESENT`), never a refusal - `task show`
is for humans/agents to *see* problems, `task validate`/`task close`
are where they actually block something. JSON output exposes the full
`TASK.json` fields, parsed SPEC.md sections, `VALIDATION.json`, and
RESULT.md presence/content; nothing raw or secret-shaped is ever
printed (every managed artifact is already scanned before it's written,
and free-text fields are additionally passed through
`forgeops.security.redact.redact_text` as defense in depth).

## Task list

Read-only, index-driven. Reports one concise row per task: ID, title,
status, approval state, validation state, result state, worktree/agent
assignment, updated timestamp. `--status STATUS` filters by exact
string match - no separate validation against the recognized-status set,
so an unrecognized filter value simply yields zero rows rather than a
refusal. Also detects (never repairs) two drift conditions, each a
`WARNINGS_PRESENT`-level warning: a **stale index entry** (indexed, but
its directory no longer exists) and an **unindexed directory** (a
`task-NNNN`-shaped directory on disk with no index record) - mirroring
`worktree list`'s own stale-registry-entry detection.

## Task validate

Read-only. The full structural/consistency check, split explicitly into
blockers and warnings (`forgeops/state/task_validate.py:TaskIssue`).
Checked: index/directory agreement, TASK.json schema and required
fields, required SPEC.md headings (blocker if missing), task ID
consistency (requested vs. TASK.json vs. index), project-root
consistency, whether `accepted_checkpoint`/`source_head` still resolves
in git (only checked when git is available - entirely skipped, not
even a warning, otherwise), status/approval-state validity, that
`worktree_id`/`agent_id` are `null` or a plain string, that Acceptance
Criteria and Required Validation are no longer placeholders (blockers -
a fresh `draft` task is expected to have both before it's ever
close-eligible), VALIDATION.json schema, RESULT.md-vs-status
consistency (a warning either direction - reachable only by manual
tampering, since the normal lifecycle never produces a mismatch), path
containment beneath the managed tasks root, duplicate task IDs in the
index, and secret-shaped content in every managed artifact (TASK.json,
SPEC.md, VALIDATION.json, RESULT.md).

Never runs project tests, never executes a validation command, never
mutates a task file, never assigns a worktree/agent, never changes
status - purely a read.

## Validation artifact (`VALIDATION.json`)

Created by `task create` with `status: not_run` and every other field
empty/`null`: `required_checks`, `observed_checks`, `test_summary`,
`compile_summary`, `diff_check_summary`, `manual_checks`,
`evidence_references`, an `approval_reference` (used only by waived
closure - see below), `updated_at`. This checkpoint only *defines* and
*validates* this artifact - it never runs anything and never advances
`status` itself. A human, another tool, or a future checkpoint updates
it directly via `forgeops.state.task_registry.save_validation_record`
(the same function tests use to simulate a decided validation result)
before a task can close. No full logs, credentials, environment dumps,
or unrestricted command output are ever stored here - concise summaries
and evidence *references* only.

## Task closure

Mutating and confirmation-gated, mirroring `forgeops worktree remove`'s
model exactly:

- neither flag: full preflight, zero mutation, `BLOCKED` with
  `data.action == "confirmation_required"`;
- `--dry-run`: identical preflight, never needs `--confirm`, zero
  mutation, reports the exact planned state transition
  (`data.planned_status`);
- `--confirm`: performs the real, single mutating closure.

A task can only close when it passes `task validate`'s own blocking
checks *and* every closure-specific condition holds: status is not
already `completed`/`failed`/`cancelled` (**no reopening** in this
checkpoint), `VALIDATION.json.status` is `passed`, `failed`, or
`waived` (never `not_run` - this checkpoint never decides that for
you), a waived validation has a non-empty `approval_reference`, a
`--result-file` was given and reads safely (exists, not a directory,
within the same size limit as spec/acceptance files, no secret-shaped
content).

Successful closure always writes `RESULT.md` (the supplied file's
content, verbatim), transitions `TASK.json.status` to `completed`
(`passed`/`waived` validation) or `failed` (`failed` validation), sets
`result_state` to `recorded`, and updates the matching `TASK_INDEX.json`
record - as close to one atomic transaction as three separate files
allow (`forgeops/state/task_close.py:apply_task_close`):

1. `RESULT.md` first (a brand-new file - cheapest to roll back).
2. `TASK.json` second (the authoritative task record).
3. `TASK_INDEX.json` last.

If step 1 or 2 fails, whatever this call created is rolled back
(best-effort) and the failure is reported as `COMMAND_EXECUTION_FAILURE`
with `data.partial_state` and a manual recovery recommendation - never
a false success. If step 3 fails *after* 1 and 2 already succeeded, the
closure is still real (`RESULT.md`/`TASK.json` are the authoritative
record) - reported as `WARNINGS_PRESENT`, mirroring exactly how
`worktree create`/`worktree remove` already treat a registry-write
failure after their own real action succeeded. Never retried
automatically, never force-replaced.

`task close` never deletes the task directory, in success or failure.

## Status transitions (this checkpoint)

```
task create:                                          -> draft
task close, validation passed or waived:  (closeable) -> completed
task close, validation failed:            (closeable) -> failed
```

"(closeable)" = `draft`/`ready`/`active`/`blocked`/`validation_pending`
- `ready`, `active`, `blocked`, and `validation_pending` are recognized
statuses (so a later approval/agent-ownership checkpoint's own
transitions don't fail this checkpoint's validation) but nothing in
*this* checkpoint ever produces them. No arbitrary status editing, no
automatic transition to `ready`/`active`, no reopening a
`completed`/`failed`/`cancelled` task - all reserved for later
checkpoints.

## Exit codes

- `REPO_NOT_FOUND` (4) / `COMMAND_EXECUTION_FAILURE` (5, git
  unavailable) as every other command.
- `BLOCKED` (2) - the read-only reference repository; an uninitialized
  project; any `task create`/`task close` preflight conflict; missing
  `--confirm` on a non-dry-run `task close`; `task show`/`task validate`
  given a task ID that cannot be located at all; any blocking finding
  from `task validate`.
- `COMMAND_EXECUTION_FAILURE` (5) - a `task create`/`task close` write
  genuinely fails partway through (partial state detected and reported,
  already-created paths rolled back where safe).
- `WARNINGS_PRESENT` (1) - `task create`/`task close` succeeded but the
  index update failed; `task list` found a stale/unindexed entry or a
  malformed index; `task show` found any non-fatal consistency issue;
  `task validate` found only warnings (no blockers).
- `SUCCESS` (0) - otherwise, including a conflict-free `--dry-run` for
  either mutating command.

## Secret handling

Every string ever written to a managed task artifact - a supplied
`--spec-file`/`--acceptance`/`--result-file`, and (defensively) the
serialized `TASK.json`/`VALIDATION.json` themselves - is scanned with
`forgeops.security.secret_scan.scan_text` before persistence; a match
blocks the write outright rather than redacting and continuing. `task
validate` re-scans every artifact on every read as an ongoing
consistency check, since a task directory could in principle be edited
directly outside the CLI.

## Explicit non-goals (this checkpoint)

No agent assignment, no agent execution, no parallel task routing, no
automatic worktree creation, no approvals workflow, no merge
orchestration, no MCP, no notifications, no deployment, no Rocky
integration, no task editing, no reopening, no arbitrary status
changes, no task deletion. `worktree_id`/`agent_id` exist in the schema
for a later checkpoint to populate, exactly like
`forgeops/state/worktree_registry.py`'s own `task_id`/`agent_id` fields
did for this one.
