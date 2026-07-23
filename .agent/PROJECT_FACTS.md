# Project Facts

Durable facts about ForgeOps that don't change often. If something here
turns out to be wrong or stale, fix it in place — this file is meant to
save re-investigation, not preserve history (that's `.agent/DECISIONS.md`).

## What this project is

ForgeOps: a reusable governance/state/testing/hooks/skills/agents toolkit
for Claude Code and Codex, installable across future projects (TrendForge,
future client sites, Rocky). Not a dashboard, not a server, not a SaaS.
Full scope, exclusions, and phase breakdown live in the operator's original
mission brief (not duplicated here); durable summaries of *decisions made*
live in `docs/architecture-decision.md` and `.agent/DECISIONS.md`.

## Repository relationship

- `C:\Users\joshd\ForgeOps` — this repo, active development.
- `C:\Users\joshd\TrendForge` — read-only reference repo. Never modified,
  never committed to, from this project. First real-world consumer of the
  ForgeOps toolkit once it's installable, as a future separate step.

## Reusable material already identified (Phase 0)

Three deterministic Python scripts, two general-purpose skills
(`repo-audit`, `completion-protocol`), and a `CLAUDE.md`/`AGENTS.md`/
`.agent/*` state convention were already proven working in TrendForge and
are the literal starting point for Phase 1+. Full detail:
`docs/reusable-components.md`. Do not re-derive this inventory from
scratch — it's already known.

## Environment

- `claude` CLI executable: available as of 2026-07-23 —
  `C:\Users\joshd\.local\bin\claude.exe`, v2.1.218 (confirmed via
  `Get-Command`/`which` and `claude --version` in both PowerShell and
  Bash). Joshua added `C:\Users\joshd\.local\bin` to his user PATH; this
  process inherits it.
- `codex` CLI executable: still not found on PATH as of 2026-07-23
  (checked via `which`/`Get-Command`, no version output). Phase 8's Codex
  adapter must be built to detect this state and continue with a mock
  executable, per the mission's explicit instruction — do not install or
  authenticate either tool without explicit operator approval.
- Primary shell: PowerShell (Windows). Bash (Git Bash) also available.
  Scripts must support Windows paths containing spaces.

## Git policy for this repo

Feature branch for implementation work; no remote configured; do not push;
do not create a remote; local commits only after coherent checkpoints.
