# Known Failures — lessons captured before Phase 1

Evidence-backed lessons from TrendForge's history (read-only inspection of
`.agent/DECISIONS.md`, `.agent/HANDOFF.md`, `tools/browser-automation/`,
and `docs/agent-audit/*`), mapped against the failure categories named in
the mission brief. These directly shape ForgeOps's hooks (Phase 5) and
CLI safety gates (Phase 2).

## Confirmed, with direct evidence in TrendForge

### Stale `HANDOFF.md`
`HANDOFF.md` itself documents going stale once: an entry notes it was
"accurate as of `main @ f70a893` but went stale after further commits
(`f8aadd8`, `3dd1822`, `bcbbbb3`, `13950ea`) landed without this file being
refreshed," with the explicit lesson "this file gets updated at the end of
every session from now on." That fix was a documented *intention*, not a
mechanism — nothing enforced it.
**ForgeOps mitigation:** a session-end hook (Phase 5) that updates
`HANDOFF.md` automatically, not a documentation reminder that depends on
the next session remembering to follow it.

### Scratch browser scripts multiplying
`tools/browser-automation/` contains 100+ one-off Playwright files
accumulated across sessions: `ff-01-login-dashboard.js` through
`ff-47-caption-preset-select.js`, plus `fix-*.js`, `diag-*.js`, and
`acceptance-*.js` variants, several clearly superseding earlier attempts
at the same check (`ff-12-remaining-checks.js` /
`ff-12b-remaining-checks.js`, `ff-15-plan-picker.js` /
`ff-15b-plan-picker.js` / `ff-15c-plan-wait.js` / `ff-15d-plan-scroll.js`).
This is the exact failure mode named in the mission brief, observed
directly rather than hypothesized.
**ForgeOps mitigation:** `forgeops cleanup` / process-cleanup skill should
flag accumulating one-off scripts outside a tracked skill directory as a
signal to consolidate or delete; browser-acceptance work (when built,
future phase) should go through a versioned skill directory, not loose
numbered scripts at the top level of `tools/`.

### Secrets entering documentation / commits
`.agent/DECISIONS.md`, entry `2026-07-21 — Credential leak caught and
mitigated`: a QA test account's password was hardcoded and committed
twice in one session — once in a subagent's recorder scene JSON (caught
pre-push, fixed via amend) and once in that session's own draft of
`MORNING-HANDOFF.md` (already pushed to `origin` before being caught).
Mitigated by rotating the account password and redacting the file going
forward; git history was **not** rewritten (would need a force-push,
which requires explicit operator approval — correctly withheld).
Documented lesson, quoted directly: "never write a real credential value
into any file this agent creates, even internal audit/handoff docs meant
only for the operator — describe credentials by name/location, never by
value."
**ForgeOps mitigation:** this is exactly why `scan_secret_patterns.py`
(ported per `docs/reusable-components.md`) must run before every commit,
not just be available to run — Phase 2's `forgeops release-check` and
Phase 5's pre-commit hook both gate on it. Also confirms the mission's
"never store secrets... in state files" rule for `.agent/*.json` is not
theoretical.

### Background services remaining active
The presence of `stop-trendforge.ps1`, `status-trendforge.ps1`, a
`.runtime/` directory (gitignored, holding "logs, PID metadata, and
runtime ownership data" per `.gitignore`'s own comment), and equivalent
`local-video-service/.runtime/` tracking indicates this was a real,
recurring problem worth building dedicated tooling for — not a
hypothetical. TrendForge's `trendforge-launcher-common.ps1` already
implements PID-file-based process tracking.
**ForgeOps mitigation:** Phase 2's `forgeops process-list` /
`PROCESS_REGISTRY.json` (Phase 3) generalizes this pattern rather than
inventing it from scratch — the PID-registry approach is proven.

### Generated artifacts almost entering Git
`git_safety_check.py`'s `DANGEROUS_PATTERNS` explicitly targets `*.mp4`
and `artifacts/presentation-demo/*` — a pattern list only gets written
after the underlying near-miss happens. The `app-workflow-recorder`
`SKILL.md` independently states "nothing under this skill's output tree
is ever staged for commit — enforced by `.gitignore` **and**
`scripts/forgeops/git_safety_check.py`" — a defense-in-depth response to
a real risk (large generated video files, or media containing an
unmasked QA account), not a hypothetical.
**ForgeOps mitigation:** `forgeops`'s generated-artifact detector (Phase
2) treats this as a first-class detector category, not an afterthought,
and defaults new project templates to gitignoring common generated-output
directories from the start.

## Named in the mission brief, not directly evidenced in TrendForge

These didn't show up in TrendForge's own history but remain requirements
because the mission names them explicitly and they are plausible failure
modes for the multi-project, multi-agent future ForgeOps is meant to
support:

- Missing or inaccurate `CURRENT_STATE.json` — TrendForge never built one
  at project-root scope (see `docs/source-audit.md`), so there's no
  history of it going stale, but its absence itself is the gap Phase 3
  closes.
- Test results not saved outside model context — TrendForge's audits
  (`docs/agent-audit/*/logs/*.log`) show the *opposite*: raw test/build
  logs were captured to disk. This is a pattern to keep, not a failure to
  fix.
- Completed work being reinvestigated — no direct evidence either way;
  mitigated structurally by `PROJECT_FACTS.md` existing precisely to
  prevent this ("don't re-derive these from scratch, they're already
  known" appears verbatim in TrendForge's `CLAUDE.md`).
- Long sessions repeatedly compacting; power interruption; overlapping
  agent edits; unsafe git operations beyond the one documented
  credential-history decision; production changes without sufficient
  evidence — no direct TrendForge evidence found. Kept as design
  requirements per the mission brief (Phase 3 outage recovery, Phase 6
  file-ownership rules, Phase 5 dangerous-command hook) rather than
  dropped for lack of a historical incident.

## One correction worth noting for calibration

`HANDOFF.md` also documents a case of *correcting* a stale belief rather
than acting on one: a note claiming R2 durable media storage wasn't
configured on Render production was found to be outdated — production
`/status` showed it working. The session flagged this explicitly rather
than propagating the stale claim. This is a good model for ForgeOps state
files generally: state should be re-verified against ground truth, not
assumed correct because it was believed last session (directly
informs the mission's "trust but verify" framing for `CURRENT_STATE.json`
consumers).
