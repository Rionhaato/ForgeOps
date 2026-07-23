# Superpowers compatibility record

Superpowers (`github.com/obra/superpowers`) is prior art only — a
third-party Claude Code skills/plugin framework, evaluated here for
useful patterns. It is **not installed**, **not cloned**, **not
executed**, and **never becomes ForgeOps' coordinator**. This document
was written from public documentation and README material only (no
plugin install, no execution) - see Sources at the bottom.

## What it is

A markdown-based Claude Code skills framework (~a dozen `SKILL.md`
files) encoding a full development methodology: brainstorm -> write
plan -> git worktree -> subagent-driven TDD implementation -> code
review -> verification -> finish/merge. Installs as a Claude Code
plugin with a `SessionStart` hook that injects a bootstrap skill
(roughly 2,000 tokens) every session. Core Superpowers has no MCP
dependency; separate third-party wrapper projects re-expose it over
MCP for non-Claude-Code hosts.

## Governance precedence

When any Superpowers-derived pattern is adopted or adapted, this order
of authority is absolute and never inverted:

1. Joshua's explicit decisions and approvals (this conversation)
2. ForgeOps security and governance rules (`docs/audit-security-model.md`
   and related)
3. this repository's `CLAUDE.md`
4. ForgeOps-native skills and hooks
5. explicitly selected third-party workflow patterns (this document)
6. Claude Code defaults

## Adopt / adapt / reject matrix

| Area | What Superpowers does | Classification | Reason |
|---|---|---|---|
| Brainstorming | Socratic dialogue refines a rough idea into a validated design before code is written | **ADOPT PATTERN** | Pure judgment step, no automation risk; complements ForgeOps' deterministic-first flow |
| Writing plans | Breaks a design into small tasks with exact file paths and per-task verification | **ADAPT INTO FORGEOPS** | Good discipline, but belongs in `.agent/HANDOFF.md`/state files, not a separate artifact format |
| Executing plans | Batch-executes plan tasks with human checkpoints between steps | **ADAPT INTO FORGEOPS** | Checkpoint idea is sound; must route through ForgeOps' existing checkpoint/handoff mechanism, never a parallel workflow engine |
| Verification before completion | Requires actually running verification and reading real output before claiming success | **ADOPT PATTERN** | Already matches `CLAUDE.md` section 12 ("definition of done") almost exactly |
| Systematic debugging | Four-phase root-cause process; forbids fixing what isn't understood | **ADOPT PATTERN** | Generic, safe engineering discipline; no conflict with any ForgeOps rule |
| Code review | Two-stage fresh-agent review (spec compliance, then quality) before merge | **ADAPT INTO FORGEOPS** | The reviewing pass must stay read-only in ForgeOps, not a write-capable spawned agent — adapt as a checklist/gate |
| Subagent-driven development | Dispatches a fresh, write-capable subagent per task | **REJECT** | Directly contradicts ForgeOps' "no write-capable parallel agents" rule |
| Worktree workflows | Auto-creates an isolated git worktree/branch per task | **DEFER** | `.agent/HANDOFF.md` already earmarks isolated worktrees as future, not-yet-built tooling — revisit then, build ForgeOps-native |
| Automatic commits | Commits at TDD cycle boundaries and branch-finish time | **REJECT** | Conflicts categorically with "no commit without explicit authorization" |
| Session-start injection | `SessionStart` hook injects ~2,000 tokens of bootstrap guidance every session | **REJECT** | Directly violates this checkpoint's "no large SessionStart injection" rule |
| Hooks | Lifecycle hooks (`hooks.json`) run scripts on session start/clear/compact | **ADAPT INTO FORGEOPS** | The deterministic-hook mechanism fits ForgeOps' "scripts before reasoning" ethos when scoped to small, non-injecting checks (see Phase F hooks in this checkpoint) |
| Skills | Modular `SKILL.md` files, discovered/invoked on demand | **EXPLICIT INVOCATION ONLY** | Useful as optional, explicitly-called procedure docs; `CLAUDE.md` remains the sole always-on rule source |
| MCP-related guidance | Core framework is MCP-free by design; third-party wrappers expose it via MCP for other hosts | **REJECT** | ForgeOps has a standing "no MCP" rule; any MCP-exposure path is out of scope regardless of how core Superpowers itself is built |

## What this record does not do

- Does not install, clone, or execute Superpowers or any of its
  companion repositories.
- Does not copy Superpowers skill file contents verbatim.
- Does not add third-party code to this repository.
- Does not change ForgeOps' standing rules on MCP, write-capable
  agents, automatic commits, or SessionStart injection — this record
  only classifies patterns against those existing rules.

## Sources consulted (public documentation only)

- `github.com/obra/superpowers`
- `github.com/obra/superpowers-skills`
- DeepWiki: Session Lifecycle and Bootstrap; Claude Code Integration
  (Skill tool and hooks)
- Marc Nuri, "Superpowers: Claude Code Skills Framework" (blog post)
