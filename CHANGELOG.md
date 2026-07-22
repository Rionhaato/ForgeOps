# Changelog

Format: newest entries at the top. Each entry references the phase of the
ForgeOps mission it corresponds to, not a semantic-versioned release —
this project doesn't have a public release cadence yet.

## Unreleased

### Phase 2C — Full Test Suite and Release Check
- Implemented `forgeops test --full`: runs the complete supported test
  suite(s) for every detected technology, ignoring changed files
  entirely. Shares the `TestPlan`/`TestCommand` model and executor with
  `--targeted` (`build_full_test_plan` alongside the existing
  `build_test_plan`); `--targeted` and `--full` are mutually exclusive
  modes of the same `forgeops test` command.
- Implemented `forgeops release-check`: a read-only release-readiness
  gate that aggregates `forgeops doctor`, `forgeops audit`, and
  `forgeops test --full` (calling their existing `run_*` functions
  directly, never reimplementing their checks) plus three new gates
  (working-tree cleanliness, branch/HEAD availability, and a
  dependency-free `python -m compileall` validation). Exposes one clear
  `release_ready` verdict and never pushes, deploys, publishes,
  configures a remote, or mutates the repository.
- 300 passing tests (178 unit, 122 integration), up from 250 - zero
  regressions to Phase 2B's accepted behavior.
- 2 new docs (`docs/release-check.md`, `docs/phase2c-validation.md`)
  plus updates to `docs/targeted-testing.md`, `docs/cli-architecture.md`,
  and `docs/cli-exit-codes.md` (documenting `exit_codes.worst()`'s first
  real use and a precedence nuance it surfaced).

### Phase 2B — Targeted Testing and Claude Project Memory
- Added a concise root `CLAUDE.md` (123 lines, 12 required sections),
  guarded by automated tests for required sections, size limit, source-of-
  truth pointers, and absence of secret patterns.
- Implemented `forgeops changed` (classified working-tree change report:
  staged/unstaged/untracked/conflicted, renames, project-area and
  technology classification, broad-impact flagging) and
  `forgeops test --targeted` (deterministic test planning + execution,
  `--plan`/`--dry-run` modes, sequential bounded-timeout execution,
  sanitized logs).
- Added top-level CLI exception handling: an unexpected internal error
  now returns exit code 6 (`INTERNAL_ERROR`) with a redacted message and
  diagnostic log instead of a raw traceback, with a `--debug`/
  `FORGEOPS_DEBUG` opt-in for local debugging.
- Hardened the `forgeops:allow-secret` marker: it now only suppresses a
  finding inside approved test/fixture zones (never production source),
  can never apply to `.env`/database/browser-state files regardless, and
  every granted exemption is now a visible, auditable finding instead of
  a silent skip.
- 250 passing tests (169 unit, 81 integration), up from Phase 2A's 146.
  Five real defects found and fixed via dogfooding/real execution during
  this phase (an invalid `git status` flag silently masking all changed-
  file detection, untracked-directory collapsing, path-quoting with
  spaces, a test-plan coverage gap, and a Windows `npm`/`.cmd`-shim
  subprocess bug) - full account in `docs/phase2b-validation.md`.
- 3 new docs (`docs/targeted-testing.md`, `docs/phase2b-validation.md`)
  plus updates to `docs/cli-architecture.md` and `docs/audit-security-model.md`.

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
