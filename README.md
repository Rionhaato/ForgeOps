# ForgeOps

ForgeOps is a reusable operating layer for Claude Code and Codex: project
state that survives context compaction and crashes, deterministic scripts
that run before model reasoning, changed-file-aware targeted testing,
lightweight lifecycle hooks, persistent specialist-agent definitions,
reusable skills, and disabled-by-default MCP integration profiles.

It is not a dashboard, a server, a database, or a SaaS product — it's a
toolkit that installs into a project (this repo included, eventually)
alongside Claude Code and Codex, and gets out of the way otherwise.

## Status

Under active implementation. Phase 2A shipped a working, tested CLI
foundation: `forgeops doctor`, `forgeops status`, and `forgeops audit` are
real, read-only, and covered by tests. Phase 2B added changed-file
detection and targeted test planning/execution (`forgeops changed`,
`forgeops test --targeted`), a project CLAUDE.md, top-level CLI exception
handling, and hardening for the secret-scanner's allowlist marker. Phase
2C added `forgeops test --full` (the complete supported suite, ignoring
changed files), `forgeops release-check` (a read-only release-readiness
gate aggregating doctor/audit/test --full into one ready/not-ready
verdict), `forgeops checkpoint`/`forgeops handoff` (deterministic,
atomic state writers for `.agent/CURRENT_STATE.json`/`.agent/HANDOFF.md`
so another Claude Code or Codex session can resume safely), and
`forgeops process-list`/`forgeops cleanup` (read-only, Windows-native
process discovery/classification, plus a conservative, dry-run-by-
default cleanup for the narrow set of processes ForgeOps can prove it's
responsible for via an atomic `.agent/runtime/PROCESS_REGISTRY.json`).
A Context-Efficiency Foundation checkpoint followed: `forgeops
resume-context` (a read-only, bounded-size compact summary for a new
session to consume instead of rereading state files), three project-local
skills, one read-only recovery subagent, two minimal deterministic
Claude Code hooks, and a read-only Superpowers compatibility record (see
`docs/context-efficiency.md`). Most recently, `forgeops init [PATH]`
(safe, deterministic bootstrap of the minimum ForgeOps governance
structure for a target project - ownership/conflict detection, atomic
writes with rollback, TrendForge protection, and non-Git support - see
`docs/project-init.md`). `forgeops worktree list`/`forgeops worktree
create` followed (safe Git worktree inspection and bounded creation
under a deterministic managed root, with `--dry-run`, conflict
preflight, partial-failure reporting, and an atomic
`.agent/runtime/WORKTREE_REGISTRY.json`), then `forgeops worktree
remove` (confirmation-gated, eligibility/dirty/busy-checked removal of
a ForgeOps-created worktree, with `--dry-run`, identity revalidation
immediately before mutation, branch preservation by default, and
opt-in non-force branch deletion via `--delete-branch` - see
`docs/worktrees.md`), followed by a persistent, schema-controlled Task
Specification Engine: `forgeops task create|show|list|validate|close`,
storing task intent, scope, acceptance criteria, validation
expectations, and final outcome under `.agent/tasks/`, outside
conversational context - deterministic task IDs, secret-shaped-content
rejection before any write, and a `task close` confirmation/atomicity
model mirroring `worktree remove`'s own. Persistent Task Ownership
followed: `forgeops task assign|unassign`, linking an existing task to
an existing, active, unassigned ForgeOps-managed worktree one-to-one -
stored only through the `worktree_id`/`task_id` fields already reserved
in `TASK.json`/`WORKTREE_REGISTRY.json`, atomic across both records,
and cross-checked by an extended `task validate`. An
Agent Ownership Foundation followed: `forgeops agent register|list|show` (a
persistent, purely declarative agent identity registry - `.agent/agents/AGENT_REGISTRY.json`
- never probing an installed CLI, never authenticating, never launching
a process) plus `forgeops task assign-agent|unassign-agent` (the same
one-to-one ownership pattern applied to tasks and agents, atomic across
`TASK.json` and `AGENT_REGISTRY.json`, independent of worktree
ownership - see `docs/agents.md`). Most recently, a Task Approval
Foundation: `forgeops task request-approval|approve|reject|
cancel-approval` - persistent, purely declarative human approval state
for an existing task (`not_requested`/`pending`/`approved`/`rejected`,
plus an append-only `approval_history` on `TASK.json`), atomic across
`TASK.json` and `TASK_INDEX.json`, an explicit `--actor` always
required (never inferred), and never executing a task, launching an
agent, or changing task status/ownership - see `docs/approvals.md`.
See `docs/architecture-decision.md` for why this project exists and what
it deliberately does not build, `docs/cli-architecture.md` for how the
CLI is put together, `docs/checkpoint-and-handoff.md` for the two state
writers, `docs/process-list-and-cleanup.md` for process discovery and
cleanup's safety model, `docs/context-efficiency.md` for the context-
efficiency layer, `docs/project-init.md` for `forgeops init`,
`docs/worktrees.md` for `forgeops worktree`, `docs/tasks.md` for
`forgeops task`, `docs/agents.md` for `forgeops agent`,
`docs/approvals.md` for task approval, and `.agent/HANDOFF.md` for
current progress.

## CLAUDE.md vs. `.agent/` state files

Two different jobs, both required, neither a substitute for the other:

- **`CLAUDE.md`** — stable operating *rules*: architecture conventions,
  safety boundaries, approval requirements, the standard validation
  commands. Changes rarely, is always loaded into an agent's context, and
  deliberately contains no commit hashes, test counts, or other volatile
  facts that would go stale immediately. See `tests/unit/test_claude_md.py`
  for the automated guarantees on this file (required sections, size
  limit, no secret patterns).
- **`.agent/CURRENT_STATE.json` / `.agent/HANDOFF.md` / `.agent/DECISIONS.md`**
  — everything that *does* change session to session: current branch/HEAD,
  what's done, what's next, blockers, and the append-only history of
  architectural decisions. Read at the start of every session per
  `CLAUDE.md`'s startup order, updated at the end of every session/phase.

If a fact belongs in both, it belongs only in `.agent/` — `CLAUDE.md`
points at these files rather than duplicating their content.

## Usage

```powershell
pip install -e ".[dev]"

forgeops doctor              # environment/foundation health checks
forgeops status                # concise repository state
forgeops audit                   # read-only security/hygiene audit
forgeops changed                   # classified working-tree change report
forgeops test --targeted             # plan + run tests for what changed
forgeops test --full                   # run the complete supported suite(s)
forgeops release-check                   # read-only release-readiness gate
forgeops checkpoint                        # write .agent/CURRENT_STATE.json
forgeops handoff                             # write .agent/HANDOFF.md (derived from checkpoint data)
forgeops process-list                          # read-only: discover/classify processes associated with this repo
forgeops cleanup                                 # dry-run by default: report cleanup candidates, act on none
forgeops resume-context                            # read-only: compact, bounded-size summary for a new session
forgeops init [PATH]                                  # bootstrap the minimum ForgeOps governance structure for a target project

forgeops <command> --json        # structured JSON instead of human-readable text
forgeops <command> --repo <path> # inspect a repository other than the current directory (init takes a positional PATH instead - see docs/project-init.md)
python -m forgeops <command>     # equivalent to the forgeops console script

forgeops test --targeted --plan     # show the plan, run nothing
forgeops test --targeted --dry-run  # show exactly what would execute, run nothing
forgeops test --full --plan            # same, for the complete suite instead of changed files
forgeops checkpoint --dry-run             # preview the state document, write nothing
forgeops handoff --dry-run                  # preview the handoff markdown, write nothing
forgeops cleanup --execute                    # actually attempt graceful termination of eligible ("managed") processes
forgeops init --dry-run                         # preview what init would create/preserve/block, write nothing
forgeops worktree list                              # read-only: repository root, worktrees, branch/HEAD, locked/prunable/registry state
forgeops worktree create NAME                          # create an isolated worktree + new branch under the managed root
forgeops worktree create NAME --dry-run                    # preview branch/path/conflicts, create nothing
forgeops worktree create NAME --branch B --base REF            # explicit branch name / base ref instead of the derived defaults
forgeops worktree remove NAME                                # confirmation-gated: preflight only, no mutation without --confirm
forgeops worktree remove NAME --dry-run                        # preview what would be removed, mutate nothing
forgeops worktree remove NAME --confirm                          # actually remove the worktree; branch is preserved by default
forgeops worktree remove NAME --delete-branch --confirm            # also delete the ForgeOps-owned branch via non-force `git branch -d`
forgeops task create TITLE                                            # create a task (status: draft) with a minimal SPEC.md template
forgeops task create TITLE --spec-file FILE --acceptance FILE           # supply a full spec and/or acceptance criteria instead of the template
forgeops task create TITLE --dry-run                                      # preview the task ID/path, write nothing
forgeops task show TASK_ID                                                  # read-only: objective, scope, acceptance criteria, status, ownership
forgeops task list                                                            # read-only: concise rows for every task; --status STATUS filters
forgeops task validate TASK_ID                                                  # read-only: blocking errors and warnings, never mutates or runs tests
forgeops task close TASK_ID --result-file FILE                                    # confirmation-gated: preflight only, no mutation without --confirm
forgeops task close TASK_ID --dry-run                                               # preview the planned completed/failed transition, mutate nothing
forgeops task close TASK_ID --result-file FILE --confirm                              # actually close - completed (passed/waived) or failed validation
forgeops task assign TASK_ID WORKTREE_NAME                                              # link an existing task to an existing, unassigned worktree (no --confirm needed)
forgeops task assign TASK_ID WORKTREE_NAME --dry-run                                      # preview the assignment, mutate nothing
forgeops task unassign TASK_ID --confirm                                                    # clear the link; --dry-run to preview, no --confirm to preflight only
forgeops agent register AGENT_ID --kind KIND                                                  # register a declarative agent identity (kind: claude|codex|specialist|rocky)
forgeops agent register AGENT_ID --kind KIND --display-name NAME --dry-run                      # preview registration with a custom display name, write nothing
forgeops agent list                                                                                # read-only: concise rows for every agent; --kind KIND filters
forgeops agent show AGENT_ID                                                                         # read-only: kind, status, capabilities, assigned task, timestamps
forgeops task assign-agent TASK_ID AGENT_ID                                                            # link an existing task to an existing, unassigned agent (no --confirm needed)
forgeops task unassign-agent TASK_ID --confirm                                                           # clear the link; --dry-run to preview, no --confirm to preflight only
forgeops task request-approval TASK_ID --actor ACTOR                                                       # request human approval (no --confirm needed); from not_requested or rejected
forgeops task approve TASK_ID --actor ACTOR --confirm                                                        # approve a pending request; --dry-run to preview, no --confirm to preflight only
forgeops task reject TASK_ID --actor ACTOR --reason TEXT --confirm                                             # reject a pending request (reason required); --dry-run to preview
forgeops task cancel-approval TASK_ID --actor ACTOR --confirm                                                    # cancel a pending request back to not_requested; --dry-run to preview
```

Every other command named in the long-term design (`agents`,
`approvals`, `validate-config`, `install`, `uninstall`) is registered
but not yet implemented — see `docs/phase2c-validation.md` for prior
recommended-next-scope notes. `forgeops worktree` implements
`list`/`create`/`remove` — no `prune`, no bulk/forced removal, no merge
orchestration yet, see `docs/worktrees.md`. `forgeops task` implements
`create`/`show`/`list`/`validate`/`close`/`assign`/`unassign`/
`assign-agent`/`unassign-agent`/`request-approval`/`approve`/`reject`/
`cancel-approval` — no task editing, reopening, deletion, automatic
worktree creation, task/agent execution, or authenticated-identity
inference yet, see `docs/tasks.md` and `docs/approvals.md`. `forgeops
agent` implements `register`/`list`/`show` — no agent execution,
session launching, disable/enable, or deletion yet, see
`docs/agents.md`.

## Layout

- `forgeops/` — the Python package: CLI, core repo/state logic, detectors,
  hook backends, testing selection, security scanning, agent/worktree
  coordination, reporting, integration profiles, and approvals.
- `shared/` — skill and agent definitions, project-instruction templates,
  and JSON schemas shared between the Claude and Codex plugin packages.
- `claude-plugin/` — the installable Claude Code plugin (agents, skills,
  hooks, MCP profiles).
- `codex-plugin/` — the installable Codex companion (instructions, skills,
  profiles).
- `installers/` — PowerShell and POSIX installers/uninstallers for both
  plugin packages, with dry-run, backup, and merge (not overwrite)
  semantics for existing `CLAUDE.md`/`AGENTS.md` files.
- `examples/` — disposable example repositories used to validate install,
  targeted testing, and release-gate behavior without touching a real
  project.
- `tests/` — unit and integration tests for `forgeops/`.
- `docs/` — audits, design decisions, validation and security reports.
- `logs/` — raw command output (gitignored; the CLI writes here so full
  logs stay off disk-limited model context).

## Design principles

- Deterministic scripts before model reasoning; agents only for bounded
  judgment calls.
- State on disk (`.agent/CURRENT_STATE.json`, `.agent/HANDOFF.md`, ...),
  never only in a conversation.
- Full logs on disk under `logs/`; concise summaries returned to agents.
- MCP integrations disabled by default, least-privilege, environment-
  variable credential references only.
- Production mutation, publishing, spending, deployment, and credential
  changes require explicit human approval — always.

See `docs/` for the full source audit, reusable-component inventory, and
security boundaries this project operates under.
