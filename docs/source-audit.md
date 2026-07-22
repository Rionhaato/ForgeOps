# Source Audit — TrendForge (read-only reference)

Phase 0 audit of `C:\Users\joshd\TrendForge` (branch `main`, HEAD
`709fe58d7409c2c412715ebc0ec00cc0f68ea864`, clean, 0 ahead/behind
`origin/main` at audit time). TrendForge was not modified to produce this
audit — read-only inspection only.

## Classification legend

- **reusable unchanged** — generic enough to copy as-is
- **reusable after generalization** — good pattern, needs TrendForge-specific
  facts/paths stripped out
- **replace** — concept is right, implementation should be rebuilt for
  ForgeOps's broader scope
- **discard** — TrendForge-specific, not applicable to ForgeOps
- **TrendForge-specific** — product/business content, out of scope
- **generated artifact** — build/runtime output, never a source of truth
- **credential or privacy risk** — sensitive; paths recorded, contents not
  inspected or copied

## Inventory

| Path | What it is | Classification |
|---|---|---|
| `CLAUDE.md`, `AGENTS.md` | Rules-only project instructions, mirrored pair, both point at `.agent/*` for current state | reusable after generalization — the *convention* (rules here, procedures in skills, state in `.agent/`) is the reusable part; content is TrendForge-specific |
| `.agent/PROJECT_FACTS.md` | Durable technical facts (DB proxy quirk, FFmpeg resolution order, AI provider architecture) | TrendForge-specific content, **reusable schema/shape** (a living "don't re-derive this" doc, distinct from `DECISIONS.md`'s append-only history) |
| `.agent/DECISIONS.md` | Append-only architecture/process decision log, dated entries | reusable unchanged (format/convention); entries themselves are TrendForge-specific |
| `.agent/HANDOFF.md` | Current session status / branch-HEAD / next action, explicitly superseded-in-place when stale | reusable after generalization — see `docs/known-failures.md` for the staleness lesson baked into its own history |
| `.agent/CURRENT_STATE.json` | Not present at top level in TrendForge (only inside audit snapshots) — state was tracked via `HANDOFF.md`/`DECISIONS.md` prose, not a machine-readable JSON file | **replace** — ForgeOps mission requires a real schema'd `CURRENT_STATE.json`; TrendForge never built one at the project-root level, only per-audit `repository_snapshot.json` |
| `scripts/forgeops/repository_snapshot.py` | Stdlib-only, read-only, always-exit-0 JSON git snapshot (branch/HEAD/ahead-behind/modified-untracked-staged counts) | **reusable unchanged** — this is the seed of `forgeops status` |
| `scripts/forgeops/git_safety_check.py` | Scans staged diff (or a ref) for dangerous filename patterns (`.env`, `*.mp4`, `*credentials*`, `*secret*`, `artifacts/presentation-demo/*`), exits non-zero with a clear message | **reusable after generalization** — pattern list is TrendForge-specific (hardcoded `artifacts/presentation-demo/`), mechanism is exactly `forgeops`'s pre-commit gate |
| `scripts/forgeops/scan_secret_patterns.py` | Greps `git ls-files` output for AWS keys / `sk-` keys / PEM private-key headers, redacts matches, exits non-zero on hit | **reusable unchanged** — pattern set should grow (JWTs, generic bearer tokens, DB URLs) but the never-print-the-match design is exactly right |
| `scripts/forgeops/README.md` | Documents the 3 scripts + explicit conventions for future scripts (stdlib-only, read-only=exit-0, gate=non-zero-on-fail, never print secrets, ~80 lines) | **reusable unchanged** — adopt these conventions verbatim for `forgeops/` |
| `.claude/skills/repo-audit/SKILL.md` | Runs the 3 scripts above into a timestamped `docs/agent-audit/<ts>/` dir + a skimmable `SUMMARY.md` | **reusable after generalization** — becomes the seed of the `repository-audit` skill and `forgeops audit` |
| `.claude/skills/completion-protocol/SKILL.md` | Pre-"done" checklist: targeted tests → full relevant suite → diff hygiene (safety scripts + manual `git status` review) → real-browser verification for UI claims → update `HANDOFF.md` | **reusable after generalization** — becomes the seed of the `completion-protocol` skill; the "browser claims require an actual browser" rule is a strong, generalizable principle |
| `.claude/skills/app-workflow-recorder/` (`SKILL.md`, `src/*.js`, `scenes/*.json`) | Playwright-driven, exact-duration demo-video recorder with resumable per-scene state, retry/fallback locators, FFmpeg assembly | `src/*.js` engine (locator fallbacks, retry/resumability, FFmpeg duration-fitting) is **reusable after generalization**; `scenes/*.json` are **TrendForge-specific** (hardcoded UI flow) and **discard** for ForgeOps 1.0 — no browser-acceptance skill is being built this phase per the mission scope, but the engine is worth noting for a future `browser-acceptance` skill |
| `.claude/agents/`, `.claude/hooks/` | Empty — no persistent subagents or hooks were built in TrendForge | nothing to reuse; confirms Phase 6/Phase 5 are net-new work for ForgeOps |
| `forgeops-agent-kit/README.md` | A prior session's own honest "what's proven vs. aspirational" index, explicitly **not** a restructured package (operator instruction at the time: stay minimal, project-local) | reusable as **primary source material** — effectively pre-digested Phase 0 input; see `docs/architecture-decision.md` for how this mission supersedes that prior minimal-scope decision |
| `tools/browser-automation/*.js` (100+ files: `ff-01-*` … `ff-47-*`, `fix-*`, `diag-*`, `acceptance-*`) | One-off Playwright debugging/exploration scripts accumulated over multiple sessions | **discard** — this is a live example of the "scratch browser scripts multiplying" failure mode named in the mission; see `docs/known-failures.md` |
| `tools/browser-automation/shots/*.png` (150+ screenshots) | Debug screenshots from the above scripts | **generated artifact** — discard |
| `tools/browser-automation/network-logs/*.json` | Captured browser network traffic | **credential or privacy risk** — path recorded only, contents not opened (may contain session/auth headers) |
| `tools/browser-automation/auth-state/{ff-qa-user,qa-user}.json` | Playwright `storageState` files (session cookies/tokens for a QA test account) | **credential or privacy risk** — path recorded only, not opened, not copied |
| `tools/browser-automation/node_modules/` | Vendored Playwright install | **generated artifact** — discard |
| `docs/agent-audit/20260720-222009/`, `docs/agent-audit/20260721-105332/` | Two prior audit runs (repo state, git risk, test-command map, deployment map, provider inventory, security observations, browser-acceptance results, fix ledger, feature-coverage matrix, raw logs) | reusable as **evidence for this audit** (see `docs/known-failures.md`); the audit *directory shape* is reusable after generalization, contents are TrendForge-specific |
| `docs/TREND_FORGE_*.md`, `docs/*-plan.md`, `docs/mvp-scope.md`, `docs/project-charter.md`, `docs/wireframe-notes.md`, `docs/user-stories.md`, etc. | TrendForge product/business documentation | **TrendForge-specific** — discard for ForgeOps purposes |
| `backend/`, `frontend/`, `local-video-service/` | The actual TrendForge application | **TrendForge-specific** — not inspected beyond top-level structure; not a source of ForgeOps patterns, this is the product ForgeOps will eventually help operate |
| `render.yaml`, `package.json` (root) | TrendForge deployment/package manifests | **TrendForge-specific** — discard |
| `backend/.env`, `backend/.env.bak-gemini-smoke-test`, `backend/.env.before-local-fix`, `frontend/.env.local` | Real environment files (gitignored) | **credential or privacy risk** — paths recorded only, contents never read |
| `backend/.env.example`, `frontend/.env.example` | Template env files (intentionally tracked) | reusable **pattern** — env-var-reference-only convention matches the mission's integration requirements directly |
| `backend/uploads/`, `local-video-service/.runtime/`, `local-video-service/.downloads/` | User-uploaded media / local model runtime state | **credential or privacy risk** / generated artifact — not inspected |
| `local-video-service/.runtime/.../oauthState-*.js(.map)` | Vendored ComfyUI frontend bundle files whose name contains "oauthState" | **not a credential** — verified as a third-party vendored JS bundle (webpack/vite chunk name), not an actual OAuth secret; noted for completeness since it matched a naming search |

## Sensitive categories found (paths only, no values)

| Category | Path(s) |
|---|---|
| Environment files (real) | `backend/.env`, `backend/.env.bak-gemini-smoke-test`, `backend/.env.before-local-fix`, `frontend/.env.local` |
| Environment files (safe templates) | `backend/.env.example`, `frontend/.env.example` |
| Playwright storage state (session auth) | `tools/browser-automation/auth-state/ff-qa-user.json`, `tools/browser-automation/auth-state/qa-user.json` |
| Captured network traffic (possible auth headers) | `tools/browser-automation/network-logs/ff-dashboard-idle-5s.json`, `tools/browser-automation/network-logs/ff-login-dashboard-all.json` |
| Local machine settings (permission state) | `.claude/settings.local.json` |
| Uploaded/user media | `backend/uploads/` |
| Local DB / model runtime | `backend/trendforge.db` (if present locally, gitignored), `local-video-service/.runtime/`, `local-video-service/.downloads/` |
| Debug screenshots (UI state, masked account info per recorder policy) | `tools/browser-automation/shots/*.png` |
| Documented historical incident (already remediated) | QA account password committed twice on 2026-07-21 — see `.agent/DECISIONS.md` entry and `docs/known-failures.md` |

No API keys, JWTs, bearer tokens, OAuth client secrets, or PEM private key
blocks were found via pattern search in tracked source files (searches ran
in `files_with_matches`-only mode; no matched text was ever printed). The
20 files matched by the `api_key`/`secret_key`/`token` pattern search are
all expected — code that reads an env var by name (e.g.
`ANTHROPIC_API_KEY = os.getenv(...)`), not embedded values.

## Checkpoint

This document, together with `docs/reusable-components.md`,
`docs/known-failures.md`, `docs/security-boundaries.md`, and
`docs/architecture-decision.md`, constitutes the Phase 0 checkpoint. See
`.agent/CURRENT_STATE.json` for the machine-readable pointer.
