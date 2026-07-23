# Changelog

Format: newest entries at the top. Each entry references the phase of the
ForgeOps mission it corresponds to, not a semantic-versioned release —
this project doesn't have a public release cadence yet.

## Unreleased

### Context-Efficiency Foundation
- Implemented `forgeops resume-context`: read-only, bounded-size
  (strict tested byte ceiling, `RESUME_CONTEXT_MAX_BYTES`) compact
  summary of repository identity, branch/HEAD, working-tree shape, the
  last recorded checkpoint phase, latest recorded test count,
  unresolved blockers, the exact next approved task, already-validated
  commands ("do not repeat"), approval boundaries, and a stop-boundary
  reminder - for a new session to consume instead of rereading
  `.agent/CURRENT_STATE.json`/`.agent/HANDOFF.md` or the repository in
  full. Deliberately avoids the tree-scan/stack-detection work
  `build_checkpoint_data` does, reading only already-computed narrative
  fields plus cheap git facts. New: `forgeops/state/resume_context.py`,
  `forgeops/cli/resume_context.py`, `forgeops/core/governance.py`
  (small constants shared by `handoff` and `resume-context`, hoisted out
  of `forgeops/cli/handoff.py` to avoid a `state` -> `cli` circular
  import).
- Added three project-local Claude Code skills
  (`.claude/skills/forgeops-resume/`, `forgeops-validate/`,
  `forgeops-completion-report/`) - small, explicitly-invoked instruction
  files that reference `CLAUDE.md`/`docs/context-efficiency.md` instead
  of restating them.
- Added one read-only subagent (`.claude/agents/forgeops-recovery-reviewer.md`)
  restricted to `Read`/`Grep`/`Glob` only (no `Bash`, `Edit`, `Write`, or
  MCP tools) - isolates exploratory recovery/audit reads from the main
  session's context.
- Added two minimal deterministic Claude Code hooks
  (`.claude/hooks/`, wired in project-local `.claude/settings.json`
  only): a `PreToolUse` safety hook blocking TrendForge mutation,
  destructive git operations, and a short catastrophic-filesystem-command
  list; a `SessionEnd` hook invoking the existing `forgeops handoff`
  writer once per session (deliberately not `Stop`, which fires after
  every turn in this Claude Code version).
- Added a read-only Superpowers compatibility record
  (`docs/superpowers-compatibility.md`) - prior-art adopt/adapt/reject
  analysis only; Superpowers is not installed, cloned, or executed.
- New doc: `docs/context-efficiency.md`. Updates to
  `docs/cli-architecture.md`, `docs/cli-exit-codes.md`, `README.md`.
- 55 new tests (473 -> 528): `resume-context` builder/CLI (clean/dirty/
  spacey-path/missing-state/malformed-state/adversarial-size/secret-
  redaction), hook behavior (TrendForge protection, destructive-git
  rejection, safe-command passthrough, malformed-input safety, bounded
  session-end write), and structural/safety-language checks for the
  skills and subagent.

### Phase 2C — Process List and Cleanup
- Implemented `forgeops process-list`: read-only, Windows-native
  discovery (PowerShell/CIM `Win32_Process` + `netstat -ano` for
  listening ports - no `psutil`, no package installed automatically) and
  classification of processes possibly associated with the target
  repository. Every process is classified as exactly one of `managed`,
  `associated`, `uncertain`, `unrelated`, or `stale_record`
  (`forgeops/detectors/process_association.py`) - a common executable
  name (`python`, `node`, `npm`, `uvicorn`, `vite`, `git`, `powershell`,
  `cmd`, ...) is never itself evidence of anything.
- Implemented `forgeops cleanup`: conservative, defaults-to-dry-run.
  Only `managed` processes (an exact `.agent/runtime/PROCESS_REGISTRY.json`
  record match on PID, repository, and start time, with an allowed
  category) are ever termination candidates - `associated` (heuristic
  command-line evidence only) is never sufficient, no matter how much of
  it accumulates. `--execute` revalidates PID identity and start time
  immediately before acting, attempts graceful termination only
  (`taskkill /PID`, never `/F`/force), waits a bounded time, and reports
  failure rather than escalating if the process is still running. Stale
  registry records (PID reused, or process no longer exists) can be
  removed safely, still gated by the same dry-run/execute default.
- New shared primitives: `forgeops/state/runtime_registry.py`
  (`.agent/runtime/PROCESS_REGISTRY.json` load/save, atomic, generalizing
  the PID/start-time tracking pattern proven in TrendForge's
  `trendforge-launcher-common.ps1` - planned back in Phase 0's source
  audit, see `docs/known-failures.md`), `forgeops/detectors/processes.py`
  (OS process enumeration; documents that `Win32_Process` exposes no
  native working-directory property, so `working_directory` is always
  `None` on Windows today), `forgeops/detectors/process_association.py`
  (the pure classification function).
- **Real bug found and fixed during manual disposable-process
  validation** (not caught by the original unit tests, which assumed the
  wrong format): `Get-CimInstance`'s `ConvertTo-Json` renders a
  `DateTime` property as the legacy .NET JSON-date form
  (`/Date(<epoch-ms>)/`), not the raw WMI `CIM_DATETIME` string
  originally assumed - `_parse_wmi_datetime()` now handles both, with a
  regression test for each format.
- **Real, honest validation finding, not a bug**: a live disposable test
  process was registered and cleanup was run with `--execute` against
  it - graceful `taskkill` (no `/F`) did not stop a plain console Python
  process within the timeout on this machine, and cleanup correctly
  reported failure without escalating, exactly as designed. No force-kill
  path exists in this checkpoint by deliberate choice.
- Command-line text is redacted **twice** (discovery layer and CLI
  reporting layer) - a real gap where the second layer wasn't redacting
  at all was caught by `test_sanitized_command_output` during
  development.
- 93 new tests (up from 380 to 473): WMI/CIM datetime parsing (both
  formats) and netstat parsing, the full classification matrix (managed/
  associated/uncertain/unrelated/stale_record, PID reuse, disallowed
  categories, common-executable-name safety, spacey paths), the registry
  load/save round-trip and schema handling, full CLI integration
  coverage for both commands (dry-run default, explicit `--execute`,
  graceful-termination success/timeout, inaccessible/already-exited
  processes, secret sanitization, atomic registry updates,
  INTERNAL_ERROR handling), and regression tests proving every existing
  command is unchanged, `cleanup` never touches a repository other than
  the one it's invoked against, and neither new command ever writes
  outside its documented path.
- New doc: `docs/process-list-and-cleanup.md`. Updates to
  `docs/cli-architecture.md`, `docs/cli-exit-codes.md`, `README.md`.

### Phase 2C — Checkpoint and Handoff
- Implemented `forgeops checkpoint`: writes a deterministic, atomic
  snapshot to `.agent/CURRENT_STATE.json` (the existing canonical
  location). Objective fields (branch, HEAD, working-tree shape,
  detected stack, remote presence) are recomputed from git/the
  filesystem every call; narrative fields a human or prior session wrote
  (mission, completed_work, blockers, next_action, ...) are carried
  forward unchanged rather than reinvented.
- Implemented `forgeops handoff`: writes a concise markdown summary to
  `.agent/HANDOFF.md` (the existing canonical location), derived from
  the exact same deterministic data `checkpoint` computes plus a small
  set of constants mirrored from `CLAUDE.md` (standard validation
  commands, approval boundaries, prohibited actions) - never from
  free-form model reasoning.
- New shared primitives: `forgeops/state/atomic_write.py` (same-
  filesystem temp-file-then-`os.replace()` atomic text writer, used by
  both commands - no partially-written state file is ever visible, even
  across a crash) and `forgeops/state/checkpoint.py`
  (`build_checkpoint_data()`, the one pure function behind both
  commands' data). `forgeops/state/schema.py` gained
  `SUPPORTED_SCHEMA_VERSIONS`/`is_supported_schema_version()` as the
  single source of truth for which `CURRENT_STATE.json` versions this
  install can safely merge - an unsupported (e.g. future) version is a
  `warning`, never a hard failure, and never guessed at.
- Both commands support `--dry-run` (preview, write nothing), `--json`/
  human output, and never touch anything outside their one documented
  file (plus the same `logs/<command>/` side-channel every other command
  already uses) - enforced by dedicated regression tests that snapshot
  the whole working tree before/after.
- 79 new tests (up from 300 to 379): atomic-write behavior (including
  simulated write failures leaving no partial file), the pure
  checkpoint-data builder (clean/dirty/staged/untracked/spacey-path/no-
  commits-yet repositories, schema versioning, secret-shaped env values
  never leaking), full CLI integration coverage for both commands
  (human/JSON/dry-run/atomic-replace/INTERNAL_ERROR/write-failure
  paths), and regression tests proving `test --targeted`/`test --full`/
  `release-check`/every inspection command are unchanged and that
  `checkpoint`/`handoff` only ever modify their documented state paths.
- New doc: `docs/checkpoint-and-handoff.md` (state-file locations,
  determinism model, atomic-write guarantee, schema/version behavior,
  consistency model between the two commands, exit codes, secret
  safety, and how a future agent should resume from a handoff). Updates
  to `docs/cli-architecture.md`, `docs/cli-exit-codes.md`, `README.md`.

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
