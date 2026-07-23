# Changelog

Format: newest entries at the top. Each entry references the phase of the
ForgeOps mission it corresponds to, not a semantic-versioned release —
this project doesn't have a public release cadence yet.

## Unreleased

### Agent Ownership Foundation
- Implemented persistent, declarative agent identity: `forgeops agent
  register AGENT_ID --kind KIND` (mutating, no `--confirm` required -
  only `--dry-run`, mirroring `task create`/`worktree create`'s own
  additive shape), `forgeops agent list` and `forgeops agent show`
  (both read-only). Deliberately does not implement launching Claude
  Code or Codex, executing a prompt, invoking a subagent, opening or
  monitoring a session/process, probing an installed CLI, authenticating
  an account, agent disable/enable, agent deletion, or capability
  population/inference - see `docs/agents.md` "Explicit non-goals".
- New registry: `.agent/agents/AGENT_REGISTRY.json`
  (`forgeops/state/agent_registry.py`, atomic, schema-versioned, single
  flat file - no per-agent directory - mirrors
  `worktree_registry.py`'s/`task_registry.py`'s fail-whole-document-
  closed-on-any-bad-record safety). Each record: `agent_id`, `kind`
  (one of `claude`/`codex`/`specialist`/`rocky`), `display_name`,
  `status` (`registered` - the only status this checkpoint ever writes
  - or `disabled`, recognized but unreachable via any command yet),
  `capabilities` (always `[]` - declarative strings only, never probed
  or inferred), `assigned_task_id`, `created_at`/`updated_at`,
  `metadata` (always `{}`).
- Agent IDs are user-supplied, never auto-generated: lowercase letters,
  digits, `-`, `_` only, length-limited, rejected outright (never
  sanitized) on any violation - the same allow-list philosophy
  `forgeops/worktrees/naming.py:validate_worktree_name` and
  `forgeops/state/task_registry.py:validate_task_id` already
  established. Both the agent ID and display name are secret-scanned
  before persistence; a match blocks registration outright.
- Extended task-to-worktree ownership (`forgeops/state/task_ownership.py`)
  with a parallel task-to-agent layer: `forgeops task assign-agent
  TASK_ID AGENT_ID` (mutating, no `--confirm` required) and `forgeops
  task unassign-agent TASK_ID` (mutating, confirmation-gated via
  `--confirm`, `--dry-run` supported, double-unassignment refused just
  like `task unassign`). One-to-one in both directions, stored only
  through `TASK.json.agent_id` and `AGENT_REGISTRY.json`'s
  `assigned_task_id` (both previously reserved, always `null`) - no
  secondary ownership database. Assignment preflight verifies task and
  agent identity/eligibility (task not terminal/not already assigned,
  agent registered/not disabled/not already assigned elsewhere,
  `TASK_INDEX.json` already agreeing with `TASK.json`) and is re-run
  verbatim immediately before mutating to close the TOCTOU gap.
- Agent ownership and worktree ownership are explicitly independent
  fields: a task with no assigned worktree yet is never blocked from
  receiving an agent - only a non-blocking `task-has-no-worktree`
  warning (`WARNINGS_PRESENT`) is reported, and assignment still
  succeeds.
- `TASK.json` and the agent's registry record are written as one atomic
  pair on both assign and unassign - a failure on the second write
  after the first succeeded rolls the first back and reports
  `COMMAND_EXECUTION_FAILURE`, never a partial assignment.
  `TASK_INDEX.json` remains bookkeeping only, updated last; its own
  failure is `WARNINGS_PRESENT`, mirroring the established
  `task assign`/`task close` pattern exactly.
- Extended `forgeops task validate` with ten read-only
  agent-ownership-consistency blockers: `agent-missing`,
  `agent-registry-malformed`, `agent-disabled-while-assigned`,
  `agent-reciprocal-mismatch`, `orphan-task-agent-ownership`,
  `orphan-registry-agent-ownership`, `duplicate-agent-assignment`,
  `invalid-agent-identifier`, `task-index-agent-mismatch`,
  `terminal-task-with-agent`.
- `task show`/`task list` human output gained explicit `assigned
  agent`/`agent ownership: assigned|unassigned` and `assigned_agent=`
  lines, alongside the existing worktree-ownership ones. JSON output
  already exposed `TASK.json`'s `agent_id` field directly.
- `forgeops/cli/agent.py` reuses `forgeops/cli/task.py`'s own
  `_repo_level_block` gate (protected-reference-repo + initialized-project
  checks) directly rather than a second copy of the same two checks -
  the first cross-module reuse of a nominally private helper in this
  codebase, done deliberately to avoid duplicating a single shared
  precondition.

### Task Ownership
- Implemented `forgeops task assign TASK_ID WORKTREE_NAME` (mutating,
  no `--confirm` required - only `--dry-run` - mirroring `task
  create`/`worktree create`'s additive, easily-reversed shape) and
  `forgeops task unassign TASK_ID` (mutating, confirmation-gated via
  `--confirm`, `--dry-run` supported, mirroring `task close`/`worktree
  remove` instead). Deliberately implements only linking an *existing*
  task to an *existing* worktree - no automatic worktree creation, no
  agent assignment, no approvals, no task execution/routing, no
  parallel execution, no merges, no MCP, no Rocky integration, no
  deployment - see `docs/tasks.md` "Explicit non-goals".
- Ownership is one-to-one and stored only through the two fields
  already reserved for it in the existing schemas -
  `TASK.json.worktree_id` and `WORKTREE_REGISTRY.json`'s per-record
  `task_id` (both previously always `null`) - no secondary ownership
  database. `WorktreeRecord` gains an `updated_at` field (also now
  populated by `worktree create`), set whenever `task_id`/`agent_id`
  change; backward-compatible (`null`) on any pre-existing record.
- Assignment preflight (`forgeops/state/task_ownership.py:build_task_assign_plan`,
  shared by `--dry-run` and a real run, and re-run verbatim immediately
  before mutating to close the TOCTOU gap) verifies: task exists and is
  schema-valid; worktree exists and its registry entry is valid; the
  worktree has not been removed, is not locked, is not the protected
  reference repository, and its Git identity still matches the
  registry (the same live-state checks `worktree remove` already
  performs); the task is not `completed`/`failed`/`cancelled`; neither
  the task nor the worktree is already assigned to something else.
- Assignment updates `TASK.json` and the worktree's registry record as
  one atomic pair - both are equally authoritative for ownership, so a
  failure on the second write after the first succeeded rolls the first
  back (best-effort) and reports `COMMAND_EXECUTION_FAILURE` with
  `partial_state` and a manual recovery recommendation, never a partial
  assignment. `TASK_INDEX.json` remains bookkeeping only, updated last;
  its own failure is `WARNINGS_PRESENT`, mirroring `task create`/`task
  close`'s established registry-write-failure pattern.
- Unassignment mirrors `task close`'s confirmation model exactly
  (preflight-only without `--confirm`, zero-mutation `--dry-run`). A
  task with no current assignment is itself a preflight conflict
  (`task-not-assigned`) - double-unassigning is refused, never a silent
  no-op. If the worktree was independently removed via `worktree
  remove` since assignment (which never clears ownership itself - see
  below), `TASK.json` is still correctly cleared even with no matching
  registry record left to clear, reported plainly rather than as a
  failure.
- `forgeops worktree remove` is deliberately unchanged - it still
  removes a worktree regardless of task assignment and never clears the
  task's `worktree_id` itself. The resulting orphaned ownership is
  exactly what the new `task validate` checks below exist to catch.
- Extended `forgeops task validate` (and therefore `task close`'s own
  preflight, which reuses it) with eight ownership-consistency checks,
  all blockers, all read-only: `worktree-missing`, `worktree-removed`,
  `ownership-mismatch`, `orphan-task-ownership`,
  `orphan-registry-ownership`, `duplicate-assignment`,
  `worktree-id-schema-mismatch`, `worktree-registry-malformed`.
- `task show`/`task list` human output now explicitly states ownership:
  `show` gains an `assigned worktree: <name>  ownership:
  assigned|unassigned` line; each `list` row gains `assigned_worktree=`.
  JSON output already exposed `worktree_id` via the existing `TASK.json`
  field pass-through - unchanged.

### Persistent Task Specifications
- Implemented a persistent, schema-controlled Task Specification
  Engine: `forgeops task create|show|list|validate|close`, storing task
  intent, scope, acceptance criteria, validation expectations, and
  final outcome under `.agent/tasks/`, outside conversational context.
  Deliberately does not implement agent assignment, agent execution,
  parallel task routing, automatic worktree creation, approvals,
  merges, task editing, reopening, or deletion - see `docs/tasks.md`
  "Explicit non-goals".
- Managed structure: `.agent/tasks/TASK_INDEX.json` (atomic,
  schema-versioned, concise summaries only - mirrors
  `worktree_registry.py`'s shape/safety, including failing the whole
  document closed on any single unreadable record) plus
  `.agent/tasks/<task-id>/{TASK.json,SPEC.md,VALIDATION.json}` per task
  (`RESULT.md` added only by a successful `task close`).
- Task IDs are deterministic and monotonic (`task-0001`, `task-0002`,
  ...) via `TASK_INDEX.json`'s own `next_task_number` counter - never
  derived from a timestamp, never silently reused. A failed creation
  attempt rolls back whatever it created in that call so the number is
  safely retried; a leftover directory from a non-rolled-back failure
  blocks the next attempt at that ID outright rather than being
  overwritten.
- `task create TITLE` (mutating, `--dry-run` supported) generates a
  minimal SPEC.md template (Objective, In Scope, Out of Scope,
  Constraints, Acceptance Criteria, Required Validation, Stop
  Boundary - every section present, requiring later human completion,
  never a large plan inferred from `TITLE`), or ingests `--spec-file`
  verbatim and/or `--acceptance FILE` (text or JSON criteria, rejected
  outright if it looks like an executable script). Every supplied file
  is read-only, size-limited, and secret-scanned
  (`forgeops.security.secret_scan.scan_text`) before ever being
  persisted - a match blocks creation outright, never redacted-and-kept.
- `task show`/`task list` are read-only. `show` reuses `task validate`'s
  own structural check but only ever refuses on a task that cannot be
  located at all - every other issue is a warning, never a block. `list`
  reports concise per-task rows and detects (never repairs) stale index
  entries and unindexed on-disk task directories, mirroring `worktree
  list`'s own drift detection.
- `task validate TASK_ID` (read-only) checks index/directory agreement,
  TASK.json/VALIDATION.json schema, required SPEC.md headings,
  ID/project-root consistency, whether the accepted checkpoint still
  resolves in git (skipped entirely when git is unavailable),
  status/approval-state validity, placeholder Acceptance
  Criteria/Required Validation sections, path containment, duplicate
  task IDs, and secret-shaped content in every managed artifact -
  reported as blockers and warnings separately. Never runs project
  tests, never executes a validation command, never mutates, never
  assigns a worktree/agent, never changes status.
- `VALIDATION.json` (created `not_run` by `task create`) is only ever
  *defined and validated* by this checkpoint - nothing in it runs
  automatically; a human/tool decides `passed`/`failed`/`waived`
  (waived requiring a recorded `approval_reference`) by writing to it
  directly before a task becomes close-eligible.
- `task close TASK_ID --result-file FILE` (mutating, confirmation-gated
  via `--confirm`, `--dry-run` supported) mirrors `worktree remove`'s
  confirmation model exactly. Closes `completed` (passed/waived
  validation) or `failed` (failed validation) - never reopens a
  terminal task. Writes RESULT.md, then TASK.json, then
  TASK_INDEX.json, in that order; a failure in the first two rolls back
  what it can and reports `COMMAND_EXECUTION_FAILURE` with
  `partial_state` and a manual recovery recommendation, while an index
  write failure *after* the real closure succeeded is `WARNINGS_PRESENT`
  - the same asymmetric-partial-failure pattern `worktree
  create`/`worktree remove` already established. Never deletes the
  task directory.
- New shared gate (`forgeops/cli/task.py:_repo_level_block`), used by
  all five commands: refuses the read-only reference repository, and
  requires an already-initialized ForgeOps project
  (`.agent/CURRENT_STATE.json` present and schema-valid) - the first
  ForgeOps commands to require this precondition.

### Safe Worktree Removal
- Implemented `forgeops worktree remove NAME` (mutating,
  confirmation-gated): safe removal of a ForgeOps-created worktree and,
  optionally, its ForgeOps-owned branch. Deliberately does not implement
  pruning, bulk/forced removal, merge orchestration, agent execution,
  task routing, or approvals - see `docs/worktrees.md` "Explicit
  non-goals".
- Confirmation model: without `--confirm`, runs the full preflight and
  reports what would be removed but performs no mutation
  (`BLOCKED`, `data.action == "confirmation_required"`); `--dry-run`
  never requires `--confirm`, never mutates, and returns the same exit
  code a confirmed run would. No interactive prompt - stays
  deterministic and automation-safe.
- Read-only preflight (`forgeops/state/worktree_remove.py:build_worktree_remove_plan`)
  collects every eligibility, dirty, and busy conflict in a single pass
  shared by `--dry-run`, the missing-`--confirm` response, and a real
  run: invalid/absolute/traversal name, unregistered or ambiguous
  (duplicate) registry state, malformed registry, registry/Git path or
  branch-identity mismatch, path outside the managed root, protected
  reference repository, active task/agent ownership, stale
  registry-only entry, primary-checkout protection, a locked worktree,
  uncommitted changes (staged/modified/untracked), an in-progress Git
  operation (merge/rebase/cherry-pick/revert/bisect), and a live,
  exactly-registered ForgeOps-managed process still associated with the
  worktree's path.
- Removal mechanics (`forgeops/state/worktree_remove.py:apply_worktree_remove`):
  revalidates worktree identity immediately before mutating (closing
  the preflight/apply gap), then a single `git worktree remove <path>`
  - never `--force`, never `git worktree prune`, never a recursive
  filesystem delete. Verifies both that Git no longer lists the path and
  that the directory is actually gone before treating the removal as
  successful; only then marks the registry record `removed` (a new
  `STATUS_REMOVED` value, not a new schema field - `WORKTREE_REGISTRY.json`
  records are preserved as concise removal/lifecycle history rather than
  deleted). A failed or unverifiable `git worktree remove` never touches
  the registry.
- Branch preserved by default. `--delete-branch --confirm` opts into a
  normal, non-force `git branch -d` only when the branch is in the
  ForgeOps-owned `forgeops/<name>` namespace, matches the registry
  record, is local and not checked out elsewhere, and its tip commit
  still matches what was captured at preflight (a TOCTOU guard) -
  **never** `git branch -D`, never a remote deletion. An unmerged
  branch's refusal by Git is surfaced as-is: the branch stays intact,
  the (already-completed) worktree removal is still reported, and the
  overall result is `WARNINGS_PRESENT` rather than a failure.
- Every partial-failure state (failed git removal, git-reported-success-
  but-still-listed, registry write failure after a verified removal, an
  identity change between preflight and apply, a refused/ineligible
  branch deletion) is reported explicitly with `data.partial_state`/
  `data.manual_recovery_recommendation` where applicable - never
  silently upgraded to success, never automatically force-cleaned.
- New git plumbing: `forgeops/worktrees/git_worktree.py:remove_worktree`
  (`git worktree remove`, never `--force`) and `delete_branch_safe`
  (`git branch -d`, never `-D`).

### Safe Git Worktree Foundation
- Implemented `forgeops worktree list` (read-only) and `forgeops
  worktree create NAME` (mutating, `--dry-run` supported): a bounded
  foundation for isolated parallel work. Deliberately does not
  implement worktree removal, pruning, merge orchestration, agent
  execution, task routing, or approvals - see `docs/worktrees.md`
  "Explicit non-goals".
- Worktrees are created only beneath a deterministic managed root,
  `<repository-parent>/.forgeops-worktrees/<repository-name>/<name>`,
  never inside the source checkout. `NAME` is validated against a
  strict allow-list (`^[A-Za-z0-9][A-Za-z0-9_-]*$`, max 100 chars, no
  absolute paths, no Windows reserved device names) and **rejected
  outright** rather than sanitized/rewritten on any violation - this
  single rule makes traversal sequences, separators, spaces, and
  absolute/drive-letter paths all impossible by construction.
- Branch naming: `--branch` omitted derives `forgeops/<name>`;
  `worktree create` always creates a **new** branch (`git worktree add
  -b`) and never reuses or resets an existing one - a requested branch
  that already exists, or is already checked out elsewhere, blocks
  creation with a distinct conflict reason for each case.
- Base ref resolution: `--base` omitted defaults to `HEAD`, resolved to
  a fixed commit SHA during preflight and passed to `git worktree add`
  as that SHA (never a movable ref name), so a concurrent branch update
  between preflight and creation cannot change what the new worktree is
  based on.
- Read-only preflight (`forgeops/state/worktree_create.py:build_worktree_create_plan`)
  collects every conflict (protected reference repo, bare repository,
  invalid name, existing destination, outside the managed root,
  duplicate worktree, branch already exists/checked out elsewhere,
  unresolvable base ref, malformed or conflicting registry state) in a
  single read-only pass shared identically by `--dry-run` and a real
  run, so both always agree on whether creation would proceed and on
  the exit code.
- Atomicity/partial-failure handling: `git worktree add` failing
  triggers detection and reporting of exactly what partial state
  remains (directory/branch/git-registration) plus a manual recovery
  recommendation - never a force-remove, `git worktree prune`, or `git
  branch -D`; cleanup of a partial failure is left to a human by
  design in this checkpoint.
- New atomic, schema-versioned registry: `.agent/runtime/WORKTREE_REGISTRY.json`
  (`forgeops/state/worktree_registry.py`, mirroring
  `runtime_registry.py`'s shape/safety properties) - one record per
  created worktree (id, name, path, branch, base commit, created
  timestamp, status; `task_id`/`agent_id` fields exist for a future
  checkpoint but are always written `null` here). Unlike the process
  registry, any single unreadable record marks the *whole* registry
  malformed (fails safe for conflict detection) rather than being
  silently skipped. `worktree list` still works from Git's own state
  when the registry is absent or malformed; `worktree create` refuses
  to proceed when it's malformed.
- `worktree list` reports, per worktree: path, HEAD, branch or detached
  state, bare/locked/locked-reason/prunable/prunable-reason, whether
  it's the primary checkout, whether it's inside the managed root, and
  whether/how it's registered with ForgeOps - parsed from `git worktree
  list --porcelain`, tolerantly (an unrecognized porcelain line degrades
  to a warning, never a crash). Also detects and reports (never
  auto-removes) stale registry entries whose path no longer appears in
  Git's own worktree list.
- New: `forgeops/worktrees/naming.py` (NAME validation, deterministic
  branch/root/path derivation), `forgeops/worktrees/git_worktree.py`
  (porcelain-list parsing, `git worktree add`, branch/ref plumbing - no
  shell interpolation anywhere), `forgeops/state/worktree_registry.py`,
  `forgeops/state/worktree_create.py` (preflight plan builder + the
  single mutating apply step), `forgeops/cli/worktree.py` (thin CLI
  handler for both subcommands).
- New doc: `docs/worktrees.md`, including a documented known limitation
  (a true bare repository has no `.git` entry anywhere, so the shared
  repo-root discovery every command reuses can never find it at all -
  `worktree create` still safely refuses via `REPO_NOT_FOUND` rather
  than a false `SUCCESS`). Updates to `docs/cli-architecture.md`,
  `docs/cli-exit-codes.md`, `README.md`.
- 710 passing tests (up from 579): porcelain-parsing matrix (single/
  multiple/detached/bare/locked-with-and-without-reason/prunable/
  malformed output), NAME validation matrix, registry load/save/
  malformed-record handling, preflight conflict matrix (every conflict
  key above, individually and against real disposable git repositories
  including one with spaces in its path), dry-run/real-run exit-code
  agreement, simulated `git worktree add` failure and partial-state
  reporting, credential-shaped values never leaking, real end-to-end
  smoke validation (dry-run, real creation, listing the result, registry
  contents, source-checkout tracked files verified unmodified, no
  remote configured), and a regression check that every existing
  command is unaffected.

### Safe Project Initialization
- Implemented `forgeops init [PATH]`: safe, deterministic bootstrap of
  the minimum ForgeOps governance structure for a target project (a
  root `CLAUDE.md` plus `.agent/CURRENT_STATE.json`,
  `.agent/PROJECT_FACTS.md`, `.agent/DECISIONS.md`,
  `.agent/HANDOFF.md`). Unlike every other command, target resolution
  never walks upward for `.git` - the target is exactly the given PATH
  (or cwd) - so `init` never silently initializes a different
  parent/child repository than the one requested.
- Ownership/conflict model: every managed path is classified
  `missing`/`compatible`/`conflict` in a read-only preflight pass before
  any write. `.agent/CURRENT_STATE.json` reuses the same schema-version
  compatibility check `checkpoint`/`handoff` already have (but is
  intentionally *more* conservative - an unsupported/malformed existing
  state blocks the whole run rather than being silently reset). The
  other four markdown files use a `<!-- forgeops:managed schema=1 -->`
  first-line ownership marker, so a second `forgeops init` run
  recognizes its own prior output as compatible (idempotent) while a
  user's own hand-written file at the same path (no marker) is reported
  as a conflict and never overwritten or merged. Any conflict blocks the
  entire run - zero files are written, even ones that would otherwise be
  fine to create.
- Atomic, all-or-nothing writes: every file write goes through the
  existing `atomic_write_text`; if any single write in a run fails,
  every path *that run* created is removed again (reverse order) before
  returning, so a failed `init` never leaves a half-initialized project.
  Pre-existing compatible paths are never touched by rollback.
- `--dry-run` runs the identical preflight and reports
  would-create/would-preserve per path without writing anything, and
  returns the **same** exit code (including `BLOCKED`) a real run would.
- Works in a directory with no `.git` at all - git is optional for this
  command alone, and the git executable is only ever invoked when a
  `.git` entry is actually present.
- Refuses to initialize the read-only reference repository
  (`C:\Users\joshd\TrendForge`) or any path beneath it
  (`forgeops.core.paths.is_protected_reference_path`) - a second,
  independent enforcement layer alongside the existing
  `.claude/hooks/pretooluse_safety.py` PreToolUse hook.
- New: `forgeops/state/project_init.py` (pure plan builder, content
  renderers, rollback-on-failure writer), `forgeops/cli/init.py` (thin
  CLI handler). `forgeops/core/paths.py` gained
  `READONLY_REFERENCE_REPO`/`is_protected_reference_path`.
- New doc: `docs/project-init.md`. Updates to `docs/cli-architecture.md`,
  `docs/cli-exit-codes.md`, `README.md`.
- 579 passing tests (up from 528): plan-classification matrix (missing/
  compatible/conflict for every managed path, malformed/unsupported-
  schema JSON, ownership-marker present/absent), atomic rollback
  (simulated write failure mid-run leaves no new paths, never touches
  pre-existing compatible ones), full CLI integration coverage (omitted/
  explicit/spacey/nonexistent/file-not-directory PATH, non-Git/Git
  detection, dry-run semantics and exit-code agreement, human/JSON
  output, idempotent second run, partial-initialization completion,
  TrendForge protection with a monkeypatched fake reference path -
  the real TrendForge checkout is never touched by any test -
  INTERNAL_ERROR handling, secret-shaped env values never leaking), and
  a regression test proving every existing command is unaffected.

### Context-Efficiency Foundation
- Implemented `forgeops resume-context`: read-only, bounded-size
  (strict tested byte ceiling, `RESUME_CONTEXT_MAX_BYTES`) compact
  summary of repository identity, branch/HEAD, working-tree shape, the
  last recorded checkpoint phase, latest recorded test count,
  unresolved blockers, the exact next approved task, already-validated
  commands ("do not repeat"), approval boundaries, and a stop-boundary
  reminder - for a new session to consume instead of rereading
  `.agent/CURRENT_STATE.json`/`.agent/HANDOFF.md` or the repository in
  full. Deliberately avoids the tree-scan/stack-detection work
  `build_checkpoint_data` does, reading only already-computed narrative
  fields plus cheap git facts. New: `forgeops/state/resume_context.py`,
  `forgeops/cli/resume_context.py`, `forgeops/core/governance.py`
  (small constants shared by `handoff` and `resume-context`, hoisted out
  of `forgeops/cli/handoff.py` to avoid a `state` -> `cli` circular
  import).
- Added three project-local Claude Code skills
  (`.claude/skills/forgeops-resume/`, `forgeops-validate/`,
  `forgeops-completion-report/`) - small, explicitly-invoked instruction
  files that reference `CLAUDE.md`/`docs/context-efficiency.md` instead
  of restating them.
- Added one read-only subagent (`.claude/agents/forgeops-recovery-reviewer.md`)
  restricted to `Read`/`Grep`/`Glob` only (no `Bash`, `Edit`, `Write`, or
  MCP tools) - isolates exploratory recovery/audit reads from the main
  session's context.
- Added two minimal deterministic Claude Code hooks
  (`.claude/hooks/`, wired in project-local `.claude/settings.json`
  only): a `PreToolUse` safety hook blocking TrendForge mutation,
  destructive git operations, and a short catastrophic-filesystem-command
  list; a `SessionEnd` hook invoking the existing `forgeops handoff`
  writer once per session (deliberately not `Stop`, which fires after
  every turn in this Claude Code version).
- Added a read-only Superpowers compatibility record
  (`docs/superpowers-compatibility.md`) - prior-art adopt/adapt/reject
  analysis only; Superpowers is not installed, cloned, or executed.
- New doc: `docs/context-efficiency.md`. Updates to
  `docs/cli-architecture.md`, `docs/cli-exit-codes.md`, `README.md`.
- 55 new tests (473 -> 528): `resume-context` builder/CLI (clean/dirty/
  spacey-path/missing-state/malformed-state/adversarial-size/secret-
  redaction), hook behavior (TrendForge protection, destructive-git
  rejection, safe-command passthrough, malformed-input safety, bounded
  session-end write), and structural/safety-language checks for the
  skills and subagent.

### Phase 2C — Process List and Cleanup
- Implemented `forgeops process-list`: read-only, Windows-native
  discovery (PowerShell/CIM `Win32_Process` + `netstat -ano` for
  listening ports - no `psutil`, no package installed automatically) and
  classification of processes possibly associated with the target
  repository. Every process is classified as exactly one of `managed`,
  `associated`, `uncertain`, `unrelated`, or `stale_record`
  (`forgeops/detectors/process_association.py`) - a common executable
  name (`python`, `node`, `npm`, `uvicorn`, `vite`, `git`, `powershell`,
  `cmd`, ...) is never itself evidence of anything.
- Implemented `forgeops cleanup`: conservative, defaults-to-dry-run.
  Only `managed` processes (an exact `.agent/runtime/PROCESS_REGISTRY.json`
  record match on PID, repository, and start time, with an allowed
  category) are ever termination candidates - `associated` (heuristic
  command-line evidence only) is never sufficient, no matter how much of
  it accumulates. `--execute` revalidates PID identity and start time
  immediately before acting, attempts graceful termination only
  (`taskkill /PID`, never `/F`/force), waits a bounded time, and reports
  failure rather than escalating if the process is still running. Stale
  registry records (PID reused, or process no longer exists) can be
  removed safely, still gated by the same dry-run/execute default.
- New shared primitives: `forgeops/state/runtime_registry.py`
  (`.agent/runtime/PROCESS_REGISTRY.json` load/save, atomic, generalizing
  the PID/start-time tracking pattern proven in TrendForge's
  `trendforge-launcher-common.ps1` - planned back in Phase 0's source
  audit, see `docs/known-failures.md`), `forgeops/detectors/processes.py`
  (OS process enumeration; documents that `Win32_Process` exposes no
  native working-directory property, so `working_directory` is always
  `None` on Windows today), `forgeops/detectors/process_association.py`
  (the pure classification function).
- **Real bug found and fixed during manual disposable-process
  validation** (not caught by the original unit tests, which assumed the
  wrong format): `Get-CimInstance`'s `ConvertTo-Json` renders a
  `DateTime` property as the legacy .NET JSON-date form
  (`/Date(<epoch-ms>)/`), not the raw WMI `CIM_DATETIME` string
  originally assumed - `_parse_wmi_datetime()` now handles both, with a
  regression test for each format.
- **Real, honest validation finding, not a bug**: a live disposable test
  process was registered and cleanup was run with `--execute` against
  it - graceful `taskkill` (no `/F`) did not stop a plain console Python
  process within the timeout on this machine, and cleanup correctly
  reported failure without escalating, exactly as designed. No force-kill
  path exists in this checkpoint by deliberate choice.
- Command-line text is redacted **twice** (discovery layer and CLI
  reporting layer) - a real gap where the second layer wasn't redacting
  at all was caught by `test_sanitized_command_output` during
  development.
- 93 new tests (up from 380 to 473): WMI/CIM datetime parsing (both
  formats) and netstat parsing, the full classification matrix (managed/
  associated/uncertain/unrelated/stale_record, PID reuse, disallowed
  categories, common-executable-name safety, spacey paths), the registry
  load/save round-trip and schema handling, full CLI integration
  coverage for both commands (dry-run default, explicit `--execute`,
  graceful-termination success/timeout, inaccessible/already-exited
  processes, secret sanitization, atomic registry updates,
  INTERNAL_ERROR handling), and regression tests proving every existing
  command is unchanged, `cleanup` never touches a repository other than
  the one it's invoked against, and neither new command ever writes
  outside its documented path.
- New doc: `docs/process-list-and-cleanup.md`. Updates to
  `docs/cli-architecture.md`, `docs/cli-exit-codes.md`, `README.md`.

### Phase 2C — Checkpoint and Handoff
- Implemented `forgeops checkpoint`: writes a deterministic, atomic
  snapshot to `.agent/CURRENT_STATE.json` (the existing canonical
  location). Objective fields (branch, HEAD, working-tree shape,
  detected stack, remote presence) are recomputed from git/the
  filesystem every call; narrative fields a human or prior session wrote
  (mission, completed_work, blockers, next_action, ...) are carried
  forward unchanged rather than reinvented.
- Implemented `forgeops handoff`: writes a concise markdown summary to
  `.agent/HANDOFF.md` (the existing canonical location), derived from
  the exact same deterministic data `checkpoint` computes plus a small
  set of constants mirrored from `CLAUDE.md` (standard validation
  commands, approval boundaries, prohibited actions) - never from
  free-form model reasoning.
- New shared primitives: `forgeops/state/atomic_write.py` (same-
  filesystem temp-file-then-`os.replace()` atomic text writer, used by
  both commands - no partially-written state file is ever visible, even
  across a crash) and `forgeops/state/checkpoint.py`
  (`build_checkpoint_data()`, the one pure function behind both
  commands' data). `forgeops/state/schema.py` gained
  `SUPPORTED_SCHEMA_VERSIONS`/`is_supported_schema_version()` as the
  single source of truth for which `CURRENT_STATE.json` versions this
  install can safely merge - an unsupported (e.g. future) version is a
  `warning`, never a hard failure, and never guessed at.
- Both commands support `--dry-run` (preview, write nothing), `--json`/
  human output, and never touch anything outside their one documented
  file (plus the same `logs/<command>/` side-channel every other command
  already uses) - enforced by dedicated regression tests that snapshot
  the whole working tree before/after.
- 79 new tests (up from 300 to 379): atomic-write behavior (including
  simulated write failures leaving no partial file), the pure
  checkpoint-data builder (clean/dirty/staged/untracked/spacey-path/no-
  commits-yet repositories, schema versioning, secret-shaped env values
  never leaking), full CLI integration coverage for both commands
  (human/JSON/dry-run/atomic-replace/INTERNAL_ERROR/write-failure
  paths), and regression tests proving `test --targeted`/`test --full`/
  `release-check`/every inspection command are unchanged and that
  `checkpoint`/`handoff` only ever modify their documented state paths.
- New doc: `docs/checkpoint-and-handoff.md` (state-file locations,
  determinism model, atomic-write guarantee, schema/version behavior,
  consistency model between the two commands, exit codes, secret
  safety, and how a future agent should resume from a handoff). Updates
  to `docs/cli-architecture.md`, `docs/cli-exit-codes.md`, `README.md`.

### Phase 2C — Full Test Suite and Release Check
- Implemented `forgeops test --full`: runs the complete supported test
  suite(s) for every detected technology, ignoring changed files
  entirely. Shares the `TestPlan`/`TestCommand` model and executor with
  `--targeted` (`build_full_test_plan` alongside the existing
  `build_test_plan`); `--targeted` and `--full` are mutually exclusive
  modes of the same `forgeops test` command.
- Implemented `forgeops release-check`: a read-only release-readiness
  gate that aggregates `forgeops doctor`, `forgeops audit`, and
  `forgeops test --full` (calling their existing `run_*` functions
  directly, never reimplementing their checks) plus three new gates
  (working-tree cleanliness, branch/HEAD availability, and a
  dependency-free `python -m compileall` validation). Exposes one clear
  `release_ready` verdict and never pushes, deploys, publishes,
  configures a remote, or mutates the repository.
- 300 passing tests (178 unit, 122 integration), up from 250 - zero
  regressions to Phase 2B's accepted behavior.
- 2 new docs (`docs/release-check.md`, `docs/phase2c-validation.md`)
  plus updates to `docs/targeted-testing.md`, `docs/cli-architecture.md`,
  and `docs/cli-exit-codes.md` (documenting `exit_codes.worst()`'s first
  real use and a precedence nuance it surfaced).

### Phase 2B — Targeted Testing and Claude Project Memory
- Added a concise root `CLAUDE.md` (123 lines, 12 required sections),
  guarded by automated tests for required sections, size limit, source-of-
  truth pointers, and absence of secret patterns.
- Implemented `forgeops changed` (classified working-tree change report:
  staged/unstaged/untracked/conflicted, renames, project-area and
  technology classification, broad-impact flagging) and
  `forgeops test --targeted` (deterministic test planning + execution,
  `--plan`/`--dry-run` modes, sequential bounded-timeout execution,
  sanitized logs).
- Added top-level CLI exception handling: an unexpected internal error
  now returns exit code 6 (`INTERNAL_ERROR`) with a redacted message and
  diagnostic log instead of a raw traceback, with a `--debug`/
  `FORGEOPS_DEBUG` opt-in for local debugging.
- Hardened the `forgeops:allow-secret` marker: it now only suppresses a
  finding inside approved test/fixture zones (never production source),
  can never apply to `.env`/database/browser-state files regardless, and
  every granted exemption is now a visible, auditable finding instead of
  a silent skip.
- 250 passing tests (169 unit, 81 integration), up from Phase 2A's 146.
  Five real defects found and fixed via dogfooding/real execution during
  this phase (an invalid `git status` flag silently masking all changed-
  file detection, untracked-directory collapsing, path-quoting with
  spaces, a test-plan coverage gap, and a Windows `npm`/`.cmd`-shim
  subprocess bug) - full account in `docs/phase2b-validation.md`.
- 3 new docs (`docs/targeted-testing.md`, `docs/phase2b-validation.md`)
  plus updates to `docs/cli-architecture.md` and `docs/audit-security-model.md`.

### Phase 2A — Safe Deterministic CLI Foundation
- Implemented `forgeops doctor`, `forgeops status`, `forgeops audit` -
  read-only, working via both the `forgeops` console script and
  `python -m forgeops`, with `--json` and `--repo <path>` support.
- Shared CLI infrastructure: repo-root discovery, git-state inspection,
  config loading, a structured `Check`/`CommandResult` model, stable exit
  codes, safe subprocess execution, deterministic timestamps.
- Stack detection (Python/pytest/FastAPI/Node/npm/pnpm/yarn/React/Vite/
  Jest/Vitest, mixed-repository detection) from manifest/marker evidence.
- Security: redaction, secret-pattern scanning (never stores/prints a
  matched value), dangerous-filename detection - generalized from
  TrendForge's `scripts/forgeops/*.py` (see `docs/phase2a-porting-notes.md`).
- 146 passing tests (101 unit, 45 integration), including an automated
  read-only guarantee for `audit`.
- 5 new docs (`docs/phase2a-porting-notes.md`, `docs/cli-architecture.md`,
  `docs/cli-exit-codes.md`, `docs/audit-security-model.md`,
  `docs/phase2a-validation.md`).

### Phase 1 — Repository Foundation
- Created the full repository structure (`forgeops/`, `shared/`,
  `claude-plugin/`, `codex-plugin/`, `installers/`, `examples/`, `tests/`,
  `docs/`, `logs/`) plus root `README.md`, `LICENSE`, `CHANGELOG.md`,
  `pyproject.toml`, and `.gitignore`.
- Created `feature/phase1-repository-foundation` for implementation work.

### Phase 0 — Source Audit
- Read-only audit of TrendForge's reusable automation patterns:
  `docs/source-audit.md`, `docs/reusable-components.md`,
  `docs/known-failures.md`, `docs/security-boundaries.md`,
  `docs/architecture-decision.md`.
