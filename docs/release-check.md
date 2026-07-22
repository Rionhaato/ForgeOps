# Release Check

`forgeops release-check` is a read-only release-readiness gate. It never
pushes, deploys, publishes, configures a remote, or mutates the target
repository — it evaluates a fixed set of gates and reports one clear
overall verdict: **RELEASE READY** or **NOT RELEASE READY**.

"Read-only" here means what it means everywhere else in ForgeOps: no
staging, committing, ignoring, quarantining, or rewriting of repository
content, and no network calls to a remote. It does not mean "no
subprocess execution" — one of the gates is the full test suite, which
genuinely runs as a real subprocess (see "Full test result" below),
exactly like `forgeops test --full` does on its own.

## Usage

```
forgeops release-check
forgeops release-check --json
forgeops release-check --repo <path>
```

No `--plan`/`--dry-run` mode exists for this command — evaluating
readiness *is* the action; there is nothing to plan separately from
doing it.

## Design: aggregate, don't duplicate

`release-check` does not reimplement doctor's, audit's, or the test
planner's logic. It calls `run_doctor(...)`, `run_audit(...)`, and
`run_full_test(...)` directly (the same `run_*` functions their own CLI
commands call) and folds each of their `checks[]` lists into its own,
prefixed by source (`doctor.*`, `audit.*`, `test.*`) so the origin of
every finding stays traceable. `doctor`/`audit` sub-calls run with
`write_log=False` (their findings are already fully represented in
release-check's own consolidated output and log; a separate doctor.log/
audit.log per release-check run would just be clutter). The `test.*`
sub-call runs with `write_log=True`, so the actual test suite's raw
stdout/stderr is preserved exactly as `forgeops test --full` would leave
it, under `logs/test/<timestamp>/`.

## Gates evaluated

| Gate | Check id | Source | Statuses |
|---|---|---|---|
| Repository discovery | `repo-discovery` | own | pass / fail (fail short-circuits before any other gate runs) |
| Working tree cleanliness | `working-tree-clean` | own | pass (clean) / warning (dirty) - a dirty tree is flagged, not a hard blocker |
| Branch and HEAD availability | `branch-head-availability` | own | pass / warning (detached HEAD) / fail (no commits at all) |
| Compile validation (Python) | `compile-validation-python` | own | pass / fail (real `python -m compileall` syntax errors) / informational (`unavailable: no Python stack detected`) |
| Compile/build validation (Node) | `compile-validation-node` | own | always informational - either `unavailable: no Node stack detected` or `unavailable: Node build validation requires installed dependencies, which ForgeOps does not install automatically`. Never attempted for real, since a real Node build essentially always needs installed `node_modules`, and ForgeOps never installs dependencies automatically (see docs/targeted-testing.md and the no-install guarantee tested throughout this project). |
| Generated-artifact findings | `audit.generated-artifact-dirs`, `audit.oversized-files` | aggregated from audit | per audit's existing policy (see docs/audit-security-model.md) - unchanged here |
| Secret-safety findings | `audit.secret-scan`, `audit.git-safety`, `audit.env-files-real`, etc. | aggregated from audit | per audit's existing policy - a secret finding is `blocked`, a hard release blocker |
| Repository health | `doctor.*` (all of doctor's checks) | aggregated from doctor | per doctor's existing policy |
| Full test result | `test.*` (all of `forgeops test --full`'s checks) | aggregated from test | per test's existing policy - a failing or non-collecting test run is `fail` |

Doctor's own optional-tool warnings (`claude`/`codex` CLI not found) flow
through unchanged too - they were never meant to block anything, and
still don't here.

## Overall verdict

```
data.release_ready: bool
data.overall_exit_code: int
data.blocking_checks: [check ids with status "blocked" or "fail"]
data.warning_checks: [check ids with status "warning"]
data.gate_results: {"doctor": {...}, "audit": {...}, "test_full": {...}}
```

`release_ready` is `True` iff the overall exit code is `SUCCESS` or
`WARNINGS_PRESENT` - i.e. no blocked, failed, invalid-config,
repo-not-found, or internal-error signal anywhere across release-check's
own gates or any aggregated sub-command. This is a **fail-closed**
design: if readiness can't be established (git missing, repo not found,
a sub-command errors), the result is NOT ready, never assumed ready.

The overall exit code is computed via the existing
`exit_codes.worst(...)` helper (defined in Phase 2A, unused until this
phase) across release-check's own gates plus doctor's, audit's, and
test's exit codes.

**Known nuance, worth calling out explicitly**: `worst()`'s documented
precedence ranks `COMMAND_EXECUTION_FAILURE` (5) as more severe than
`BLOCKED` (2). If a repository has *both* a leaked secret (blocked) *and*
a failing test suite (command execution failure) in the same run, the
single `exit_code`/`overall_exit_code` integer reflects the
`COMMAND_EXECUTION_FAILURE`, not `BLOCKED` - but **`blocking_checks` in
the JSON output still lists both**, so nothing is hidden; only the
single-integer exit code can't represent two simultaneous "worst"
reasons at once. Always check `data.blocking_checks` for the complete
picture, not just the numeric exit code, when more than one gate can
plausibly be failing at once. This precedence is inherited unchanged
from Phase 2A's `forgeops/core/exit_codes.py` (release-check is simply
the first real caller of `worst()`) and was not altered as part of this
phase, consistent with the instruction to preserve accepted architecture.

## Raw logs

`logs/release-check/<timestamp>/release-check.log` - a redacted,
line-per-check summary of every gate (repository snapshot info, every
aggregated check, the overall verdict).

`logs/release-check/<timestamp>/compileall.log` - the real `python -m
compileall` stdout/stderr, redacted, only written when a Python stack was
detected.

`logs/test/<timestamp>/python-pytest.log` / `node-test.log` - the full
test suite's own raw output, written by the aggregated `run_full_test`
sub-call exactly as `forgeops test --full` would write it on its own.

## What this command explicitly does not do

- Does not push, deploy, publish, or configure a git remote.
- Does not stage, commit, ignore, quarantine, or rewrite anything in the
  repository.
- Does not install any dependency, for any language, under any
  circumstance (inherited from `forgeops test --full`'s own guarantee).
- Does not attempt a real Node build/bundler run - see the
  `compile-validation-node` gate above.
- Does not execute a release, a deployment, or any external action -
  it only assesses and reports readiness.
