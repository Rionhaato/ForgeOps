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
See `docs/architecture-decision.md` for why this project exists and what
it deliberately does not build, `docs/cli-architecture.md` for how the
CLI is put together, `docs/checkpoint-and-handoff.md` for the two state
writers, `docs/process-list-and-cleanup.md` for process discovery and
cleanup's safety model, and `.agent/HANDOFF.md` for current progress.

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

forgeops <command> --json        # structured JSON instead of human-readable text
forgeops <command> --repo <path> # inspect a repository other than the current directory
python -m forgeops <command>     # equivalent to the forgeops console script

forgeops test --targeted --plan     # show the plan, run nothing
forgeops test --targeted --dry-run  # show exactly what would execute, run nothing
forgeops test --full --plan            # same, for the complete suite instead of changed files
forgeops checkpoint --dry-run             # preview the state document, write nothing
forgeops handoff --dry-run                  # preview the handoff markdown, write nothing
forgeops cleanup --execute                    # actually attempt graceful termination of eligible ("managed") processes
```

Every other command named in the long-term design (`init`, `worktree`,
`agents`, `approvals`, `validate-config`, `install`, `uninstall`) is
registered but not yet implemented — see `docs/phase2c-validation.md`
for prior recommended-next-scope notes.

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
