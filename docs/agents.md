# Agent Ownership Foundation (`forgeops agent register|list|show`, `forgeops task assign-agent|unassign-agent`)

Persistent, declarative agent identity and one-to-one task-to-agent
ownership. This checkpoint implements only registration, listing,
showing, assignment, and unassignment - see "Explicit non-goals" below
for what is deliberately out of scope.

## Where this lives

There is no `forgeops/agents/` package. The canonical implementation is:

- `forgeops/state/agent_registry.py` - `AGENT_REGISTRY.json` schema,
  atomic load/save, `agent_id` validation
- `forgeops/state/agent_register.py` - `agent register` preflight + write
- `forgeops/state/task_ownership.py` - task↔agent assignment/unassignment
- `forgeops/state/task_validate.py` - agent-ownership consistency checks
- `forgeops/cli/agent.py` - the three `run_*` handlers

An empty `forgeops/agents/` package created by the Phase 1 scaffolding
once claimed this responsibility; it was removed because the work landed
under `forgeops/state/` and nothing ever imported it. See
`docs/cli-architecture.md` "Package ownership".

## Commands

```
forgeops agent register AGENT_ID --kind KIND [--repo PATH]
forgeops agent register AGENT_ID --kind KIND --display-name NAME [--repo PATH]
forgeops agent register AGENT_ID --kind KIND --dry-run [--repo PATH]
forgeops agent register AGENT_ID --kind KIND --json [--repo PATH]

forgeops agent list [--repo PATH]
forgeops agent list --kind KIND [--repo PATH]
forgeops agent list --json [--repo PATH]

forgeops agent show AGENT_ID [--repo PATH]
forgeops agent show AGENT_ID --json [--repo PATH]

forgeops task assign-agent TASK_ID AGENT_ID [--repo PATH]
forgeops task assign-agent TASK_ID AGENT_ID --dry-run [--repo PATH]
forgeops task assign-agent TASK_ID AGENT_ID --json [--repo PATH]

forgeops task unassign-agent TASK_ID [--repo PATH]
forgeops task unassign-agent TASK_ID --dry-run [--repo PATH]
forgeops task unassign-agent TASK_ID --confirm [--repo PATH]
forgeops task unassign-agent TASK_ID --json [--repo PATH]
```

`forgeops agent` with no subcommand is a plain argparse usage error
(exit 2), matching every other two-level ForgeOps subcommand.

## Precondition: an initialized ForgeOps project

Every agent command reuses `forgeops.cli.task._repo_level_block` - the
same shared gate the Task Specification Engine already established:
refuses the read-only reference repository (TrendForge), and requires
`.agent/CURRENT_STATE.json` to already exist and be schema-valid (run
`forgeops init` first).

## What an agent record is (and is not)

A registered agent is a **declarative identity record only**:

- an ID, a kind, a display name, a status, an (always-empty, for this
  checkpoint) `capabilities` list of declarative strings, and current
  task assignment;
- nothing about whether Claude Code or Codex is actually installed on
  this machine - `forgeops agent register` never probes for one;
- no credential, environment variable, authentication state, prompt,
  session identifier, or executable command is ever stored, requested,
  or inferred.

Registering an agent never launches a process, opens a session,
authenticates an account, installs anything, or enables MCP. Nothing in
this checkpoint executes a prompt, invokes a subagent, monitors a
process, or routes work to an agent - it only records that an identity
*exists* and *may currently own one task*.

## Agent registry

`.agent/agents/AGENT_REGISTRY.json` (`forgeops/state/agent_registry.py`),
schema-versioned and atomically written, mirroring
`forgeops/state/worktree_registry.py`'s and
`forgeops/state/task_registry.py`'s shape and safety properties. A
single flat file - no per-agent directory or subdirectory:

```json
{
  "schema_version": 1,
  "records": [
    {
      "schema_version": 1,
      "agent_id": "claude-primary",
      "kind": "claude",
      "display_name": "claude-primary",
      "status": "registered",
      "capabilities": [],
      "assigned_task_id": null,
      "created_at": "…",
      "updated_at": "…",
      "metadata": {}
    }
  ]
}
```

Any single unreadable record marks the *whole* registry malformed
(same fail-closed-for-mutation reasoning as the worktree and task
registries: "no matching ID" must reliably mean "no matching ID," never
"one existed and was silently dropped"). `agent register` refuses
outright on a malformed registry; `agent list`/`agent show` still work
where possible, reporting it as a warning/blocked result respectively,
never repairing it automatically.

## Agent identifiers

User-supplied, never auto-generated - `forgeops/state/agent_registry.py:validate_agent_id`
enforces a strict allow-list, rejected outright (never sanitized) on
any violation: lowercase letters, digits, `-`, `_` only, starting with
a letter or digit, at most 64 characters, non-empty. This single rule
makes traversal sequences, absolute paths, and whitespace all
impossible by construction, mirroring
`forgeops/worktrees/naming.py:validate_worktree_name` and
`forgeops/state/task_registry.py:validate_task_id`. Both the agent ID
and the display name are additionally scanned for secret-shaped content
(`forgeops.security.secret_scan.scan_text`) before ever being
persisted - a match blocks registration outright.

## Supported kinds and statuses

Kinds: `claude`, `codex`, `specialist`, `rocky` - any other value is
rejected at registration (`invalid-kind`).

Statuses: `registered` (every new agent's starting and, in this
checkpoint, only reachable status) and `disabled` (recognized by
validation and by the assignment preflight - "a disabled agent cannot
be assigned" - but nothing in this checkpoint ever sets it; no
`agent disable`/`agent enable` command exists yet).

## Registration

`forgeops agent register AGENT_ID --kind KIND` is mutating but,
mirroring `forgeops task create`/`forgeops worktree create`'s own
shape, needs no `--confirm` - only `--dry-run` (identical preflight,
zero mutation, same exit code a real run would give). `--display-name`
defaults to the agent ID itself when omitted; both it and the ID are
size-limited and secret-scanned as described above. `capabilities` and
`metadata` are always written as `[]`/`{}` this checkpoint - declarative
strings only, never inferred from the local machine or an installed
CLI, and there is no CLI flag to populate them yet.

A successful registration updates only
`.agent/agents/AGENT_REGISTRY.json` - a single atomic write (unlike
`task create`/`worktree create`, there is no per-agent directory or
secondary file, so there is nothing to roll back on partial failure;
a failed write simply reports `COMMAND_EXECUTION_FAILURE` with nothing
written).

## Ownership (`forgeops task assign-agent`/`forgeops task unassign-agent`)

One-to-one in both directions: one task owns at most one agent, one
agent owns at most one task. Stored only through the fields already
reserved for this exact purpose:

- `TASK.json.agent_id` (`forgeops/state/task_registry.py`, present and
  always `null` since `task create` was implemented);
- `AGENT_REGISTRY.json`'s per-record `assigned_task_id`
  (`forgeops/state/agent_registry.py`).

`TASK_INDEX.json`'s own `agent_id` summary field remains bookkeeping
only, exactly as every other task field there - never the source of
truth. No secondary ownership database.

### Assignment preflight

`forgeops/state/task_ownership.py:build_task_assign_agent_plan` is
read-only, shared by `--dry-run` and a real run (and re-run verbatim by
`apply_task_assign_agent` immediately before mutating, closing the
preflight/apply TOCTOU gap):

- task exists, is schema-valid, and its own identity is trustworthy
  (`task-not-found` / `duplicate-task-id` / `task-json-malformed` /
  `task-id-mismatch` / `task-project-root-mismatch`);
- task status is not already `completed`/`failed`/`cancelled`
  (`task-already-terminal`);
- task is not already assigned an agent (`task-already-has-agent`);
- `TASK_INDEX.json`'s own `agent_id` already agrees with `TASK.json`
  (`task-index-mismatch` otherwise - an existing drift is never
  silently assigned over);
- agent exists, its registry entry is valid, and its status is
  `registered`, not `disabled` (`agent-not-found` /
  `duplicate-agent-id` / `agent-registry-malformed` / `agent-disabled`);
- the agent is not already assigned to a *different* task
  (`agent-already-assigned`).

**Agent ownership and worktree ownership are independent fields.** A
task with no assigned worktree yet is never blocked from receiving an
agent - only a non-blocking `task-has-no-worktree` warning
(`WARNINGS_PRESENT`, not `BLOCKED`) is reported, and the assignment
still proceeds.

### Assignment mechanics

No `--confirm` gate, for the same additive/reversible reasoning as
`task assign`/`agent register`. `TASK.json` and the agent's registry
record are updated as one atomic pair - both are equally authoritative
for agent ownership, so a failure on the second write after the first
succeeded rolls the first back (best-effort) and reports
`COMMAND_EXECUTION_FAILURE` with `data.partial_state` and a manual
recovery recommendation - **never** a partial assignment.
`TASK_INDEX.json` is updated last; its own failure is
`WARNINGS_PRESENT`, mirroring `task assign`/`task close`'s established
pattern exactly.

### Unassignment

Confirmation-gated, mirroring `task unassign`'s model exactly: without
`--confirm`, full preflight and zero mutation (`BLOCKED`,
`data.action == "confirmation_required"`); `--dry-run` needs no
`--confirm`, never mutates. A task with no current agent assignment is
itself a preflight conflict (`task-not-assigned-agent`) -
**double-unassigning is refused, never a silent no-op** (the same
explicit choice `task unassign` already made for worktree ownership,
kept consistent here rather than introducing a second convention).

Confirmed unassignment clears `TASK.json.agent_id` back to `null` and
(if a matching registry record still exists) the agent's
`assigned_task_id` back to `null` too - the same atomic-pair/index-last
model as assignment. Never deletes or disables the agent, never
terminates a process, never changes task status, never touches a
worktree or Git.

## List and show

Both read-only. `agent list` reports one concise row per agent - ID,
kind, display name, status, assigned task, updated timestamp - and
supports `--kind KIND` filtering (exact match; an unrecognized filter
value simply yields zero rows, no separate validation). `agent show`
adds capabilities and both timestamps. An unknown agent ID returns a
stable `BLOCKED` "not found" result (`data.found = false`), mirroring
`task show`'s own convention for the same situation. A malformed
registry is reported via a warning check (`list`) or a blocking check
(`show`) - never mutated, never auto-repaired. Human and JSON output
agree semantically; no raw registry file content or secret-shaped value
is ever printed.

## Task presentation

`task show` gained an `assigned agent: <id or None>  agent ownership:
assigned|unassigned` line, alongside the existing worktree-ownership
line. `task list` rows gained `assigned_agent=<id or None>`. JSON output
already exposed `TASK.json`'s `agent_id` field directly - unchanged.

## Validation extensions (`task validate`)

`forgeops/state/task_validate.py:_check_agent_ownership_consistency`
adds these blockers to every `task validate TASK_ID` run:

| Check | Meaning |
|---|---|
| `agent-missing` | `TASK.json.agent_id` is set but no such agent is registered at all. |
| `agent-registry-malformed` | `AGENT_REGISTRY.json` itself could not be read safely - fails closed. |
| `agent-disabled-while-assigned` | The referenced agent's status is `disabled`, not `registered`. |
| `agent-reciprocal-mismatch` | The referenced agent's `assigned_task_id` points at a *different* task. |
| `orphan-task-agent-ownership` | The referenced agent's `assigned_task_id` is `null` (doesn't reciprocate). |
| `orphan-registry-agent-ownership` | An agent claims this task, but this task's own `agent_id` is `null`. |
| `duplicate-agent-assignment` | More than one agent claims this same task. |
| `invalid-agent-identifier` | `TASK.json.agent_id` is set but not a validly-formed agent ID. |
| `task-index-agent-mismatch` | `TASK_INDEX.json`'s `agent_id` disagrees with `TASK.json`'s. |
| `terminal-task-with-agent` | Task status is `completed`/`failed`/`cancelled` but still has an assigned agent. |

Purely read-only - never repairs anything it finds, never mutates,
never runs project tests, never assigns a worktree/agent.

## Exit codes

- `REPO_NOT_FOUND` (4) / `COMMAND_EXECUTION_FAILURE` (5, git
  unavailable) as every other command.
- `BLOCKED` (2) - the read-only reference repository; an uninitialized
  project; any `agent register`/`task assign-agent`/`task
  unassign-agent` preflight conflict; `--confirm` missing on a
  non-dry-run `task unassign-agent`; `agent show` given an ID that
  cannot be located; any blocking finding from `task validate`
  (including the agent-consistency checks).
- `COMMAND_EXECUTION_FAILURE` (5) - an `agent register`/`task
  assign-agent`/`task unassign-agent` write genuinely fails partway
  through (already-written paths rolled back where safe).
- `WARNINGS_PRESENT` (1) - `agent list` found a malformed registry;
  `task assign-agent`/`task unassign-agent` succeeded but the
  `TASK_INDEX.json` update failed; `task assign-agent` succeeded on a
  task with no assigned worktree (the `task-has-no-worktree` warning).
- `SUCCESS` (0) - otherwise, including a conflict-free `--dry-run` for
  any mutating command.

## Explicit non-goals (this checkpoint)

No launching Claude Code or Codex, no executing a prompt, no invoking a
subagent, no opening or monitoring a session/process, no heartbeats, no
automatic worktree creation, no task routing, no approvals workflow, no
parallel execution, no merge orchestration, no MCP, no hooks, no
notifications, no deployment, no Rocky integration, no agent
disable/enable command, no agent deletion, no capability
population/inference, no probing an installed CLI, no authentication,
no global Claude/Codex configuration changes.
