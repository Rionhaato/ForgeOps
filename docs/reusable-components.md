# Reusable Components — carried forward from TrendForge

Concrete porting plan for Phase 1+. Everything here was proven working in
TrendForge (per `forgeops-agent-kit/README.md`'s own "built and validated,
not aspirational" claims, cross-checked by reading the actual files). None
of it is copied verbatim into this document — this is a manifest, not a
duplicate of the source.

## 1. Deterministic scripts → `forgeops/core` + `forgeops/cli`

| TrendForge source | ForgeOps destination | Change needed |
|---|---|---|
| `scripts/forgeops/repository_snapshot.py` | `forgeops/core/git_state.py`, exposed via `forgeops status` | Generalize: no hardcoded `parents[2]` repo-root assumption — use proper root discovery (walk up for `.git`); keep the always-exit-0, stdlib-only, JSON-out design unchanged |
| `scripts/forgeops/git_safety_check.py` | `forgeops/security/dangerous_diff_scan.py`, wired into `forgeops release-check` and a pre-commit hook | Generalize `DANGEROUS_PATTERNS`/`SAFE_EXCEPTIONS` out of hardcoded TrendForge paths (`artifacts/presentation-demo/*`) into a project-configurable list with sane stack-aware defaults |
| `scripts/forgeops/scan_secret_patterns.py` | `forgeops/security/secret_scan.py`, wired into `forgeops audit` and `forgeops release-check` | Expand `PATTERNS`: add generic Bearer-token shape, JWT shape, DB connection strings, GCP/Azure key shapes alongside the existing AWS/OpenAI-style/PEM patterns. Keep the redact-don't-print design unchanged — this is a hard requirement, not a nice-to-have |

Design conventions to inherit verbatim (from `scripts/forgeops/README.md`):
stdlib-only, no network calls, read-only scripts always exit 0, gate
scripts exit non-zero with a human-readable stderr message, never print a
secret value even while scanning for one, keep scripts small and
independently runnable (skills/CLI call them, never reimplement them).

## 2. Skills

| TrendForge source | ForgeOps destination | Change needed |
|---|---|---|
| `.claude/skills/repo-audit/SKILL.md` | `shared/skills/repository-audit/SKILL.md` | Generalize the fixed 3-script sequence into a stack-aware sequence (Phase 4 targeted-test selection feeds in here for non-audit runs); keep the "minimum audit files" contract (`repository_snapshot.json`, `secret_scan.txt`, `SUMMARY.md`) and the "skimmable, don't pad" instruction |
| `.claude/skills/completion-protocol/SKILL.md` | `shared/skills/completion-protocol/SKILL.md` | Generalize the hardcoded interpreter paths (`backend/.venv/windows311/Scripts/python.exe`, `npm test` from `frontend/`) into stack-detected commands; keep the 5-part structure (targeted tests → full suite → diff hygiene → real-browser verification for UI claims → update handoff) unchanged, it's a strong, general checklist |
| `.claude/skills/app-workflow-recorder/src/*.js` (engine only: `locator.js`, `action-runner.js`, `recorder.js`, `assemble.js`, `ffmpeg-locate.js`, `ffprobe.js`, `state.js`) | **not ported in ForgeOps 1.0** | Out of scope per the mission (no `browser-acceptance` skill build-out this phase); noted here so a future session doesn't re-derive the retry/fallback-locator + FFmpeg-exact-duration engine from scratch — it already exists and works |

## 3. Project-state convention → Phase 3

| TrendForge source | ForgeOps destination | Change needed |
|---|---|---|
| `CLAUDE.md` / `AGENTS.md` mirrored pair (rules only, point at `.agent/*`, note what's tracked vs. gitignored in `.claude/`) | `shared/templates/CLAUDE.md.template`, `shared/templates/AGENTS.md.template` | Strip TrendForge facts (Render/Vercel, FastAPI/React specifics); keep the structural convention: rules here, procedures in skills, state in `.agent/`, and the explicit "don't assume state from memory, check `.agent/*`" instruction |
| `.agent/PROJECT_FACTS.md` (durable facts, edit-in-place) | `shared/templates/PROJECT_FACTS.md.template` | Empty template with the "durable facts that don't change often, fix in place, don't preserve history here" header comment |
| `.agent/DECISIONS.md` (append-only, dated) | `shared/templates/DECISIONS.md.template` | Empty template with the dated-entry format header |
| `.agent/HANDOFF.md` (current status, explicitly superseded-in-place pattern, with a hard-won "update this every session" rule baked in after it went stale once — see `docs/known-failures.md`) | `shared/templates/HANDOFF.md.template` | Empty template; the "update at end of every session" discipline becomes an actual hook (Phase 5 session-end hook), not just a documented intention this time |
| `.agent/CURRENT_STATE.json` | **new** — TrendForge never built this at project-root scope | Build per the Phase 3 schema in the mission spec (repo identity, branch, HEAD, remote sync, changed files, mission, completed work, tests, blockers, active agents, owned files, worktrees, approvals, processes, checkpoint, next action, schema version) |

## 4. `.gitignore` conventions worth carrying forward

- Un-ignoring `.claude/agents/`, `.claude/skills/`, `.claude/hooks/` at the
  directory level (via `.claude/*` + explicit negations) while keeping
  `.claude/settings.local.json` ignored — documented in TrendForge's
  `DECISIONS.md` as a real gotcha (a blanket `.claude/` ignore blocks git
  from traversing in at all, so negations alone don't work). Apply this
  from day one in ForgeOps's own `.gitignore` for `claude-plugin/`.
- Generated-artifact patterns scoped by directory (`backend/generated/`,
  `artifacts/presentation-demo/`) rather than by extension alone, so
  intentionally-tracked sample media isn't accidentally excluded.

## 5. Explicitly not carried forward

- `forgeops-agent-kit/` itself as a directory name/shape — it was an
  intentionally minimal index, not the real package structure; ForgeOps
  1.0 builds the actual `shared/`, `claude-plugin/`, `codex-plugin/`,
  `installers/` tree the prior session deferred.
- Any TrendForge product content, business docs, or application code.
- `tools/browser-automation/*` one-off scripts, screenshots, network logs,
  and auth-state files — see `docs/known-failures.md`.
