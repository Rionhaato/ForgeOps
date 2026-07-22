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
real, read-only, and covered by 146 passing tests. See
`docs/architecture-decision.md` for why this project exists and what it
deliberately does not build, `docs/cli-architecture.md` for how the CLI
is put together, and `.agent/HANDOFF.md` for current progress.

## Usage (Phase 2A)

```powershell
pip install -e ".[dev]"

forgeops doctor              # environment/foundation health checks
forgeops status               # concise repository state
forgeops audit                  # read-only security/hygiene audit

forgeops <command> --json        # structured JSON instead of human-readable text
forgeops <command> --repo <path> # inspect a repository other than the current directory
python -m forgeops <command>     # equivalent to the forgeops console script
```

Every other command named in the long-term design (`init`, `checkpoint`,
`handoff`, `changed`, `test`, `release-check`, `process-list`, `cleanup`,
`worktree`, `agents`, `approvals`, `validate-config`, `install`,
`uninstall`) is registered but not yet implemented — see
`docs/phase2a-validation.md` for the exact recommended Phase 2B scope.

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
