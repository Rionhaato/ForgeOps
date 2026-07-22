# Changelog

Format: newest entries at the top. Each entry references the phase of the
ForgeOps mission it corresponds to, not a semantic-versioned release —
this project doesn't have a public release cadence yet.

## Unreleased

### Phase 2A — Safe Deterministic CLI Foundation
- Implemented `forgeops doctor`, `forgeops status`, `forgeops audit` -
  read-only, working via both the `forgeops` console script and
  `python -m forgeops`, with `--json` and `--repo <path>` support.
- Shared CLI infrastructure: repo-root discovery, git-state inspection,
  config loading, a structured `Check`/`CommandResult` model, stable exit
  codes, safe subprocess execution, deterministic timestamps.
- Stack detection (Python/pytest/FastAPI/Node/npm/pnpm/yarn/React/Vite/
  Jest/Vitest, mixed-repository detection) from manifest/marker evidence.
- Security: redaction, secret-pattern scanning (never stores/prints a
  matched value), dangerous-filename detection - generalized from
  TrendForge's `scripts/forgeops/*.py` (see `docs/phase2a-porting-notes.md`).
- 146 passing tests (101 unit, 45 integration), including an automated
  read-only guarantee for `audit`.
- 5 new docs (`docs/phase2a-porting-notes.md`, `docs/cli-architecture.md`,
  `docs/cli-exit-codes.md`, `docs/audit-security-model.md`,
  `docs/phase2a-validation.md`).

### Phase 1 — Repository Foundation
- Created the full repository structure (`forgeops/`, `shared/`,
  `claude-plugin/`, `codex-plugin/`, `installers/`, `examples/`, `tests/`,
  `docs/`, `logs/`) plus root `README.md`, `LICENSE`, `CHANGELOG.md`,
  `pyproject.toml`, and `.gitignore`.
- Created `feature/phase1-repository-foundation` for implementation work.

### Phase 0 — Source Audit
- Read-only audit of TrendForge's reusable automation patterns:
  `docs/source-audit.md`, `docs/reusable-components.md`,
  `docs/known-failures.md`, `docs/security-boundaries.md`,
  `docs/architecture-decision.md`.
