# Task Approval Foundation (`forgeops task request-approval|approve|reject|cancel-approval`)

Persistent human approval state for an **existing** task. This
checkpoint implements only requesting, approving, rejecting, and
cancelling a pending approval request, plus read-only presentation and
validation - see "Explicit non-goals" below for what is deliberately
out of scope.

## Where this lives

There is no `forgeops/approvals/` package. The canonical implementation
is:

- `forgeops/state/task_registry.py` - approval states, the closed
  `APPROVAL_TRANSITIONS` table, `ApprovalEvent`, `approval_history`,
  `validate_actor`
- `forgeops/state/task_approval.py` - preflight-plan builders and the
  mutating apply steps for all four commands
- `forgeops/state/task_validate.py` - read-only approval-consistency checks
- `forgeops/cli/task.py` - the four `run_task_*` handlers

An empty `forgeops/approvals/` package created by the Phase 1
scaffolding once claimed this responsibility; it was removed because the
work landed under `forgeops/state/` and nothing ever imported it. Note
that the separate top-level `forgeops approvals` **command** is a
pre-existing not-yet-implemented placeholder and is unrelated. See
`docs/cli-architecture.md` "Package ownership".

## Commands

```
forgeops task request-approval TASK_ID --actor ACTOR [--repo PATH]
forgeops task request-approval TASK_ID --actor ACTOR --reason TEXT [--repo PATH]
forgeops task request-approval TASK_ID --actor ACTOR --dry-run [--repo PATH]
forgeops task request-approval TASK_ID --actor ACTOR --json [--repo PATH]

forgeops task approve TASK_ID --actor ACTOR [--repo PATH]
forgeops task approve TASK_ID --actor ACTOR --reason TEXT [--repo PATH]
forgeops task approve TASK_ID --actor ACTOR --confirm [--repo PATH]
forgeops task approve TASK_ID --actor ACTOR --dry-run [--repo PATH]
forgeops task approve TASK_ID --actor ACTOR --json [--repo PATH]

forgeops task reject TASK_ID --actor ACTOR --reason TEXT [--repo PATH]
forgeops task reject TASK_ID --actor ACTOR --reason TEXT --confirm [--repo PATH]
forgeops task reject TASK_ID --actor ACTOR --reason TEXT --dry-run [--repo PATH]
forgeops task reject TASK_ID --actor ACTOR --reason TEXT --json [--repo PATH]

forgeops task cancel-approval TASK_ID --actor ACTOR [--repo PATH]
forgeops task cancel-approval TASK_ID --actor ACTOR --reason TEXT [--repo PATH]
forgeops task cancel-approval TASK_ID --actor ACTOR --confirm [--repo PATH]
forgeops task cancel-approval TASK_ID --actor ACTOR --dry-run [--repo PATH]
forgeops task cancel-approval TASK_ID --actor ACTOR --json [--repo PATH]
```

`--actor` is required on all four commands. `--reason` is optional on
`request-approval`/`approve`/`cancel-approval`; `reject` refuses to
mutate anything without a non-empty `--reason` (see "Reason handling"
below).

## Precondition: an initialized ForgeOps project

Every approval command reuses `forgeops.cli.task._repo_level_block` -
the same shared gate the Task Specification Engine and Agent Ownership
Foundation already established: refuses the read-only reference
repository (TrendForge), and requires `.agent/CURRENT_STATE.json` to
already exist and be schema-valid (run `forgeops init` first).

## What approval is (and is not)

Approval is a **purely declarative human signal recorded against an
existing task** - nothing more:

- it never executes a task, launches Claude Code or Codex, opens a
  session, or routes work to an agent;
- it never creates a worktree, merges code, pushes, deploys, or spends
  money;
- it never changes `TASK.json.status`, `worktree_id`, or `agent_id` -
  those remain entirely under `task create`/`task close`/`task
  assign`/`task assign-agent`'s control;
- it never infers *who* the approving human is - every action takes an
  explicit `--actor` value. No OS username, no Git identity, no session
  token is ever consulted. `joshua` is used throughout this project's
  own disposable tests/examples as a stand-in actor, not a default
  identity ForgeOps assumes.

## Approval states

`TASK.json.approval_state` (already reserved by the Task Specification
Engine, previously always `not_requested`) now recognizes four values:

| State | Meaning |
|---|---|
| `not_requested` | No approval has ever been requested, or a prior request was cancelled. Starting state for every task. |
| `pending` | A request is outstanding, awaiting a human `approve`/`reject`. |
| `approved` | A human approved the task. Terminal for this checkpoint - no further mutation is possible without a new checkpoint. |
| `rejected` | A human rejected the task. Recoverable: a fresh `request-approval` moves it back to `pending`. |

## State machine

`forgeops/state/task_registry.py:APPROVAL_TRANSITIONS` is the single,
closed source of truth - a lookup miss means "refused," not "assumed
invalid input." Both the mutation layer
(`forgeops/state/task_approval.py`) and the read-only validator
(`forgeops/state/task_validate.py`, by replaying `approval_history`
through the same table) consult it, so there is exactly one place that
defines what is possible:

```
not_requested --[request-approval]--> pending
rejected      --[request-approval]--> pending
pending       --[approve]----------> approved
pending       --[reject]-----------> rejected
pending       --[cancel-approval]--> not_requested
```

Everything else is refused with `invalid-approval-transition`,
including: `approved -> approved` (already approved), `approved ->
pending` (no reopening an approved task), `rejected -> approved`
without an intervening fresh request, `not_requested -> approved`,
`pending -> pending` (duplicate request), and any approval mutation
against a task whose `status` is already `completed`/`failed`/
`cancelled` (`task-already-terminal`). There is no "reopen an approved
task" command in this checkpoint.

## Actor and reason handling

`--actor` is validated by `forgeops/state/task_registry.py:validate_actor`
- non-empty, at most 100 characters, starting with a letter or digit,
containing only letters, digits, spaces, `.`, `@`, `-`, or `_`. Rejected
outright (never sanitized), the same allow-list philosophy as
`validate_task_id`/`validate_agent_id`. `--reason` is free text, at
most 2000 characters (`MAX_APPROVAL_REASON_LENGTH`). Both are
additionally scanned for secret-shaped content
(`forgeops.security.secret_scan.scan_text`) before ever being
persisted - a match blocks the action outright, the same rule already
applied to task titles, spec content, and display names.

`reject` is the one command where `--reason` is required: an empty or
whitespace-only reason blocks the action (`reason-required`) before any
file is touched. The other three commands accept an empty reason.

## Confirmation and dry-run

`request-approval` is additive/reversible (a `pending` request can
always be cancelled) - no `--confirm` needed, mirroring `task assign`/
`task assign-agent`/`agent register`. `approve`, `reject`, and
`cancel-approval` are confirmation-gated, mirroring `task close`/`task
unassign`/`task unassign-agent`: without `--confirm`, full preflight
runs and reports `data.action == "confirmation_required"` with zero
mutation (`BLOCKED`); `--dry-run` needs no `--confirm`, runs the
identical preflight, and reports the planned transition with zero
mutation and the same exit code a confirmed run would produce.

## Approval history

`TASK.json.approval_history` is a new, append-only list living
alongside `approval_state`. Each successful action adds exactly one
event:

```json
{
  "action": "requested",
  "actor": "joshua",
  "timestamp": "2026-07-23T15:45:32Z",
  "reason": "",
  "reference": null
}
```

- `action` is one of `requested`/`approved`/`rejected`/`cancelled`
  (past tense, matching the CLI verb that produced it).
- `timestamp` is always server/clock-generated
  (`forgeops.core.timestamps.iso_now`) - there is no way to supply one
  from the CLI, so history can never be backdated or forged.
- `reference` is always `null` this checkpoint - reserved for a future
  external ticket/PR reference, never populated or required yet.
- History is never edited, reordered, or deleted, and there is no
  command that erases it. `task validate` treats any mutation attempt
  it can detect (a mismatch between `approval_state` and what replaying
  `approval_history` implies) as a blocker, never repairs it.

## Persistence and atomicity

Unlike `task assign`/`task assign-agent` (where `TASK_INDEX.json` is
bookkeeping-only and its own write failure is a warning), an approval
mutation treats `TASK.json` and `TASK_INDEX.json` as an **atomic pair**:
`TASK.json` is written first (new `approval_state`, appended
`approval_history` entry, `updated_at`), then `TASK_INDEX.json`'s
matching `approval_state`/`updated_at`. If the index write fails after
`TASK.json` already succeeded, `TASK.json` is rolled back (best-effort)
and the whole call reports `COMMAND_EXECUTION_FAILURE` with
`data.partial_state` and a manual recovery recommendation - **never** a
"succeeded with a warning" result. Approval state must never appear
authoritative in one file and stale in the other, even transiently.
Every mutation re-runs its complete preflight immediately before
writing (the same TOCTOU-closing pattern every other ForgeOps mutation
uses) and returns deterministic conflict keys on any drift detected in
between.

`task status`, `worktree_id`, `agent_id`, `validation_state`,
`result_state`, `SPEC.md`, `VALIDATION.json`, and `RESULT.md` are never
touched by any approval command.

## Preflight

Every approval mutation verifies, in order: initialized ForgeOps
project; not TrendForge/protected reference repo (both via the shared
`_repo_level_block` gate); task exists, is schema-valid, and its
`TASK_INDEX.json` entry agrees with `TASK.json`
(`task-not-found`/`duplicate-task-id`/`task-json-malformed`/
`task-index-malformed`); task status is not already terminal
(`task-already-terminal`); current `approval_state` is a recognized
value (`invalid-approval-state`); the requested transition is permitted
from the current state (`invalid-approval-transition`); `TASK_INDEX.json`'s
own `approval_state` already agrees with `TASK.json`'s
(`task-index-approval-mismatch`); actor is valid, size-limited, and not
secret-shaped (`invalid-actor`/`actor-secret-detected`); reason is
size-limited and not secret-shaped
(`invalid-reason`/`reason-secret-detected`), and (for `reject` only)
non-empty (`reason-required`).

## Task show / list / JSON output

`task show` gained four lines: current `approval_state`, and (when at
least one history event exists) the most recent action, actor,
timestamp, and reason. `task list` rows already included
`approval_state` (reserved since the Task Specification Engine
checkpoint) - unchanged in shape, now populated with real values.
`task show --json`/`data.task` already exposes `TASK.json` directly via
`to_dict()`, so structured `approval_state` and the full
`approval_history` array are present automatically - no separate
"history" endpoint was added.

## Validation extensions (`task validate`)

`forgeops/state/task_validate.py:_check_approval_consistency` adds
these read-only checks to every `task validate TASK_ID` run:

| Check | Meaning |
|---|---|
| `invalid-approval-state` | `TASK.json.approval_state` is not one of the four recognized values. |
| `task-index-approval-mismatch` | `TASK_INDEX.json`'s `approval_state` disagrees with `TASK.json`'s. |
| `terminal-task-with-pending-approval` | Task status is terminal but `approval_state` is still `pending`. |
| `approval-history-invalid-action` | A history event's `action` is not one of the four recognized values. |
| `approval-history-invalid-actor` | A history event's `actor` fails the same validation `--actor` is held to. |
| `approval-history-secret-actor` | A history event's `actor` appears to contain secret-shaped content. |
| `approval-history-invalid-timestamp` | A history event's `timestamp` is not a recognized RFC3339 UTC timestamp. |
| `approval-history-missing-required-reason` | A `rejected` event has no reason recorded. |
| `approval-history-oversized-reason` | A history event's `reason` exceeds the 2000-character limit. |
| `approval-history-secret-reason` | A history event's `reason` appears to contain secret-shaped content. |
| `approval-history-impossible-transition` | Replaying `approval_history` through `APPROVAL_TRANSITIONS` hits a state/action pair that table does not permit (covers duplicate and out-of-order events). |
| `approval-state-history-mismatch` | `TASK.json.approval_state` does not match the state implied by replaying `approval_history` from `not_requested` - covers "approved without an approved event," "pending without a requested event," and every other state/history disagreement in one check. |

Purely read-only - never repairs anything it finds, never mutates,
never runs project tests, never assigns a worktree/agent, never changes
approval state.

## Exit codes

- `REPO_NOT_FOUND` (4) / `COMMAND_EXECUTION_FAILURE` (5, git
  unavailable, or an approval write genuinely fails partway through -
  already-written paths rolled back where safe) as every other command.
- `BLOCKED` (2) - the read-only reference repository; an uninitialized
  project; any preflight conflict on `request-approval`/`approve`/
  `reject`/`cancel-approval` (including `reason-required` and every
  `invalid-approval-transition` case); `--confirm` missing on a
  non-dry-run `approve`/`reject`/`cancel-approval`; any blocking finding
  from `task validate` (including the approval-consistency checks).
- `SUCCESS` (0) - a successful approval mutation (never
  `WARNINGS_PRESENT` - see "Persistence and atomicity" above, an index
  write failure is always rolled back to full failure, never a partial
  success), or a conflict-free `--dry-run`.

## Explicit non-goals (this checkpoint)

No task execution, no launching Claude Code or Codex, no creating a
session, no routing an agent, no automatic worktree creation, no
merging code, no pushing, no deploying, no enabling MCP, no sending a
message or notification, no publishing content, no spending money, no
accessing credentials, no Rocky integration, no authenticated
user/role system (actor is explicit-only, never inferred), no approval
queue outside the task's own `TASK.json`, no global approval database,
no reopening an `approved` task, no editing or deleting
`approval_history`, no automatically marking a task `ready`/`active`
once approved.
