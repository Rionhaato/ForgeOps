# Security Boundaries

Rules governing this project's relationship to TrendForge and to secrets in
general, established during Phase 0 and binding for every later phase.

## TrendForge access boundary

- `C:\Users\joshd\TrendForge` is read-only reference material for this
  project. No file in it has been or will be modified, and no commits will
  be made there.
- TrendForge's application code, business/product docs, database, uploads,
  and generated media are **not** reusable material — only the
  automation/tooling patterns listed in `docs/reusable-components.md` are.
- Nothing under TrendForge's gitignored paths was opened: real `.env`
  files, `backend/trendforge.db`, `backend/uploads/`, Playwright
  `storageState` files, or captured network logs. Their existence and
  location is recorded in `docs/source-audit.md`; their contents were
  never read.
- Verified early in this session (before any Phase 0 work): TrendForge's
  git state (`main` @ `709fe58d7409c2c412715ebc0ec00cc0f68ea864`, clean,
  0 ahead/behind `origin/main`) was unchanged before and after ForgeOps's
  own directory was corrected from an earlier bad state — confirming no
  cross-contamination occurred.

## Secret categories recognized (see `docs/source-audit.md` for the full path table)

| Category | Handling rule |
|---|---|
| `.env` / `.env.*` (real) | Never read, never staged, never referenced by value. `.env.example` templates are the only tracked env-shaped files. |
| Database files (`*.db`, `*.sqlite*`) | Never opened. Excluded from secret-pattern text scanning by extension (binary/opaque). |
| Playwright `storageState` / auth-state JSON | Never opened. Treated as live session credentials. |
| Captured network logs | Never opened — may contain auth headers or cookies. |
| API keys / tokens (AWS `AKIA...`, OpenAI-style `sk-...`, Google `AIza...`, generic bearer, JWT) | Detected via pattern match only; matched files are recorded, matched *text* is never printed — this repo's own secret scanner (ported from TrendForge, see `docs/reusable-components.md`) enforces the same rule going forward. |
| PEM private key blocks | Same as above — pattern-detected, never printed. |
| OAuth client secrets / refresh tokens | Same as above; none found in TrendForge's tracked source during this audit. |
| Uploaded/user media, DB rows | Out of scope entirely — ForgeOps operates on repository/process/test state, never on application data. |
| `.claude/settings.local.json` | Machine-local permission state — inherited convention: always gitignored, never templated with real values. |

## Rules for ForgeOps's own operation going forward

1. State files (`CURRENT_STATE.json`, `HANDOFF.md`, etc., Phase 3) never
   contain secret values or full raw logs — pointers to redacted summaries
   only, full logs live under `logs/` (also gitignored).
2. Every mutating CLI command supports `--dry-run`. Production mutation,
   credential access, publishing, and spending are disabled by default and
   require explicit approval, per the mission's approval-boundary
   requirements (Phase 6/9/10/11).
3. The secret scanner and dangerous-diff scanner (both ported from
   TrendForge, see `docs/reusable-components.md`) are wired into
   `forgeops release-check` and the pre-commit hook (Phase 5) — not just
   documented as something a human/agent should remember to run. This is
   a direct response to the credential-leak incident recorded in
   `docs/known-failures.md`: a documented reminder alone was insufficient
   once, so this time it's a gate.
4. MCP integrations (Phase 9) default to disabled, least-privilege,
   read-only, environment-variable-referenced credentials only — no
   integration in this project will hold or display a real token during
   Phase 0–14 implementation work.
5. Codex adapter (Phase 8): if/when Codex is installed, authentication
   state is checked for presence only, never displayed.

## Historical incident on record (TrendForge, not this project)

Summarized without values, per the rule above: a QA test account's
password was hardcoded and committed twice in one TrendForge session (one
instance caught pre-push and amended away, one instance already pushed to
`origin` before being caught). Remediated by rotating the account
credential and redacting the file; git history was not rewritten because
that requires a force-push and explicit operator approval, which was
correctly not sought/granted retroactively. Full account of the lesson is
in `docs/known-failures.md`; the source record is TrendForge's own
`.agent/DECISIONS.md` (read, not modified).
