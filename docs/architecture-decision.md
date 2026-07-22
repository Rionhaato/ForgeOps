# Architecture Decision — Phase 0

## Context

TrendForge already contains a prior, real attempt at ForgeOps-style
tooling: 3 deterministic Python scripts, 2 proven skills
(`repo-audit`, `completion-protocol`) plus one large but
TrendForge-specific one (`app-workflow-recorder`), and a working
`CLAUDE.md`/`AGENTS.md`/`.agent/*` state convention. That prior session's
own `forgeops-agent-kit/README.md` and `.agent/DECISIONS.md` record an
explicit operator decision at the time: *"ForgeOps stays project-local
tonight, no generic platform"* and *"packaging kept as an index, not a
restructured kit"* — deliberately deferring the full multi-project
toolkit because time that session was better spent on TrendForge itself,
the demo recorder, and a credential-leak incident.

## Decision

This mission explicitly supersedes that prior scope decision. Where the
TrendForge session chose minimalism because building a generic platform
wasn't that session's priority, this mission's priority *is* the generic,
reusable, cross-project toolkit — Joshua's stated goal is a ForgeOps 1.0
that installs into TrendForge, future websites, future apps, and
eventually Rocky, not a TrendForge-only convenience layer.

Consequently:

1. **Build the full structure**, not an index. `forgeops-agent-kit/`'s
   "point at where things actually live" approach is rejected for this
   project — ForgeOps needs to be installable into *other* repos, so its
   scripts/skills/agents must be generalized and packaged, not referenced
   in place inside TrendForge.
2. **Keep the proven core, discard the scope boundary.** The 3 scripts,
   2 general-purpose skills, and `.agent/` convention are adopted as the
   literal starting point (see `docs/reusable-components.md`) — they're
   proven, not hypothetical. What's discarded is the *decision to stop
   there*, not the work itself.
3. **Respect the mission's own exclusions**, which are narrower than
   "build everything": no web dashboard, orchestration server, database,
   cloud SaaS, or full social platform in ForgeOps 1.0; no autonomous
   publishing or live trading; Phase 10/11 (business workspaces, Rocky)
   are schemas/interfaces only. This mission is broader than TrendForge's
   prior minimal scope but is still explicitly bounded — "full toolkit"
   does not mean "unbounded scope."
4. **Python for shared logic, thin wrappers per-shell**, matching both
   the mission's design principles and TrendForge's own precedent
   (`scripts/forgeops/*.py` are already stdlib-only Python called from
   PowerShell launchers like `record-demo.ps1`). No new pattern needed
   here — extend what already works.
5. **State schema is new work, not a port.** TrendForge never built a
   project-root `CURRENT_STATE.json` (see `docs/source-audit.md`) — its
   state lived in `HANDOFF.md`/`DECISIONS.md` prose, which is exactly why
   `HANDOFF.md` was able to go stale (`docs/known-failures.md`). Phase 3
   builds the schema'd, atomically-written JSON file TrendForge was
   missing, rather than copying a pattern that already showed its own
   failure mode.
6. **Hooks are new work, not a port.** TrendForge's `.claude/hooks/` is
   empty — every safety check there was run manually. Phase 5 turns the
   "run `scan_secret_patterns.py` before every push" *discipline*
   (proven necessary by the credential-leak incident) into an enforced
   hook, closing the gap between "documented as a rule" and "actually
   happens every time."

## Consequence for repository layout

`ForgeOps/` (this repo) will hold the generalized package (`forgeops/`,
`shared/`, `claude-plugin/`, `codex-plugin/`, `installers/`, `examples/`,
`tests/`, `docs/`) per Phase 1's specified tree — not a copy of
TrendForge's application code, and not an index pointing back into
TrendForge. TrendForge remains the first real-world *consumer* of this
toolkit once it's installable (a future, separate step requiring its own
approval — not part of this build).
