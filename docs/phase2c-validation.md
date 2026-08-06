# Phase 2C Validation

## Test totals

```
python -m pytest tests -q
300 passed in ~41s

python -m pytest tests/unit -q         -> 178 passed
python -m pytest tests/integration -q  -> 122 passed
```

Up from Phase 2B's 250 (169 unit / 81 integration): +50 tests for
`build_full_test_plan` (9), `run_full_test` (14), `forgeops release-check`
(18), and top-level exception handling for the two new commands (9).

## Quality gate commands run, in order, with results

> **Point-in-time record (2026-07-22).** The results below were accurate
> when measured at this checkpoint, including gate 3's clean audit and
> gate 6's `RELEASE READY`. Both later drifted: starting with `2955b25`
> (2026-07-22, after this run), subsequent checkpoints added
> secret-shaped test fixtures without the `forgeops:allow-secret` marker,
> so `forgeops audit` began exiting 2 with 20 blocked findings and
> `forgeops release-check` reported NOT RELEASE READY. Those findings were
> all synthetic test inputs — no real credential, no rotation, no history
> rewrite — and were annotated in the 2026-07-25 Synthetic Secret-Fixture
> Hygiene checkpoint, restoring `58 informational, 12 pass (exit 0)` and
> `RELEASE READY - 0 blocker(s), 2 warning(s) (exit 1)`. This section is
> preserved as written; see `.agent/DECISIONS.md` for the correction.

1. **Full test suite**: `python -m pytest tests -q` -> 300 passed, zero regressions to the 250 tests accepted at the Phase 2B checkpoint.
2. **Syntax validation**: `python -m compileall -q forgeops tests` -> exit 0.
3. **`forgeops audit` against ForgeOps itself**: `38 informational, 12 pass (exit 0)`.
4. **`forgeops status` against ForgeOps itself**: correct branch/HEAD/stack, exit 0.
5. **`forgeops test --full` against ForgeOps itself**: real execution, ForgeOps's own 300-test suite ran via a real subprocess, `1 command(s) run, 1 succeeded (exit 0)`.
6. **`forgeops release-check` against ForgeOps itself**: `RELEASE READY - 0 blocker(s), 3 warning(s) (exit 1)` - the three warnings are a dirty working tree (expected, mid-implementation) and the two pre-existing optional-tool warnings (`claude`/`codex` not on PATH).
7. **`git diff --check`**: exit 0, no whitespace errors.
8. **TrendForge unchanged**: `git -C TrendForge status --short` empty; `git -C TrendForge rev-parse HEAD` -> `709fe58d7409c2c412715ebc0ec00cc0f68ea864`, identical to every prior phase.
9. **No remote configured**: `git remote -v` empty.
10. **Nothing pushed**: no `git push` was ever run this phase (no remote exists to push to).

## Representative CLI invocations (human + JSON)

```
$ forgeops test --full --plan
forgeops test
summary: full plan: 1 command(s) (exit 0)
plan:
  [broad/high] python -m pytest  (cwd=.)
      reason: full suite requested (forgeops test --full)

$ forgeops test --full
forgeops test
summary: full: 1 command(s) run, 1 succeeded (exit 0)
execution:
  [OK] python -m pytest (cwd=.) - 24.484s
      log: logs/test/20260722-122149/python-pytest.log

$ forgeops release-check --json
{
  "command": "release-check",
  "exit_code": 1,
  "summary": "RELEASE READY - 0 blocker(s), 3 warning(s) (exit 1)",
  "data": {
    "release_ready": true,
    "overall_exit_code": 1,
    "blocking_checks": [],
    "warning_checks": ["working-tree-clean", "doctor.claude-executable", "doctor.codex-executable"],
    "gate_results": {
      "doctor": {"exit_code": 1, "summary": "7 pass, 2 warning, 1 informational (exit 1)"},
      "audit": {"exit_code": 0, "summary": "34 informational, 12 pass (exit 0)"},
      "test_full": {"exit_code": 0, "summary": "full: 1 command(s) run, 1 succeeded (exit 0)"}
    }
  }
}
```

## Manual scenario validation (disposable repos, not ForgeOps or TrendForge)

- **Clean repo, real passing suite** -> `RELEASE READY`, exit 1 (optional-tool warnings only).
- **A real secret present** (`KEY = 'AKIA...'`) -> `NOT RELEASE READY`, exit 2 (`BLOCKED`), `audit.secret-scan` in `blocking_checks`; the secret value never appeared anywhere in human or JSON output.
- **A real failing test** (`assert False`) -> `NOT RELEASE READY`, exit 5, `test.test-execution` in `blocking_checks`.
- **A bare repo (zero commits)** -> `NOT RELEASE READY`, exit 5, `branch-head-availability` reported `fail`.
- **A Python syntax error** -> `NOT RELEASE READY`, exit 5, `compile-validation-python` reported `fail` with the real `compileall` exit code; raw output preserved in `logs/release-check/<timestamp>/compileall.log`.

## What validation actually caught

**The `exit_codes.worst()` precedence nuance** (not a bug, but a real,
previously-dormant design detail): `worst()` was defined in Phase 2A but
had no real caller until `release-check` became the first one. Its
documented precedence ranks `COMMAND_EXECUTION_FAILURE` above `BLOCKED` -
so a repository with both a leaked secret and a failing test suite
reports exit 5, not 2. This surfaced immediately while writing
`test_audit_secret_finding_is_a_hard_blocker`: the first version of that
test used a fixture repo with a secret but no actual test file, so
`pytest` itself exited 5 ("no tests collected"), which out-ranked the
expected `BLOCKED` result. The fix was to the *test fixture* (add a real
passing test), not to `release-check` or to `exit_codes.worst()` itself -
the precedence is pre-existing Phase 2A architecture, and changing it
was out of scope for this bounded checkpoint. Documented explicitly in
`docs/release-check.md` and `docs/cli-exit-codes.md` so it's never a
silent surprise: `data.blocking_checks` always lists every blocking
finding regardless of which one the single exit-code integer reflects.

No other real defects were found this phase - Phase 2C's implementation
reused Phase 2A/2B's already-hardened building blocks (`run_doctor`,
`run_audit`, the test planner/executor, the CLI dispatch/exception
boundary) directly rather than reimplementing them, which is exactly
what the mission's "aggregate, don't duplicate" instruction was meant to
produce: less new surface area for new bugs to hide in.

## Known limitations carried forward

- The `exit_codes.worst()` precedence nuance above.
- `compile-validation-node` is always `informational` / `unavailable` -
  a real Node build/bundle validation is never attempted, since it
  requires installed dependencies and ForgeOps never installs
  dependencies automatically. See `docs/release-check.md`.
- One Python group and one Node group per repo, not per project (see
  `docs/targeted-testing.md`) - inherited from Phase 2B's stack
  detection, more visible now that `--full`/`release-check` always
  exercise the whole repo rather than just changed files.
- No linter/type-checker configured (carried over from Phase 2A/2B).
- `forgeops test` has no mode beyond `--targeted`/`--full`; there is no
  "run only these specific files regardless of change detection" mode.

## Exact recommended next scope

Per the mission's explicit exclusion list for this checkpoint (checkpoint/
handoff writers, process-list/cleanup, `forgeops init`, agents,
worktrees, approvals, MCP, installers, Codex delegation, Rocky,
business-operation modules) and the natural dependency order:

1. **`forgeops checkpoint` / `forgeops handoff`** - Phase 3 state-file
   *writers*, building on `forgeops/state/schema.py`'s validation
   (read-only so far) and the `.agent/CURRENT_STATE.json` schema already
   defined in `shared/schemas/current_state.schema.json`. These are the
   most natural next step: `release-check` now produces exactly the kind
   of structured, aggregated result a checkpoint writer would want to
   persist.
2. **`forgeops process-list` / `forgeops cleanup`** - net-new process
   registry, no dependency on Phase 2C's work.
3. **`forgeops init`** - writes the `.agent/*` and `CLAUDE.md` template
   files.
4. **`forgeops worktree`, `forgeops agents`, `forgeops approvals`,
   `forgeops validate-config`, `forgeops install`/`uninstall`** remain
   gated behind Phase 3 state schemas and Phase 6/9/12 design work.

Recommend the next checkpoint open with `forgeops checkpoint`/`handoff`,
since `release-check`'s aggregated, structured readiness result is
immediately useful state for a checkpoint writer to persist, and both
commands build on validation, not mutation, of the same `.agent/`
convention already established.
