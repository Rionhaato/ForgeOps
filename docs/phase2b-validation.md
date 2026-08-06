# Phase 2B Validation

## Test totals

```
python -m pytest tests -q
250 passed in ~24s

python -m pytest tests/unit -q         -> 169 passed
python -m pytest tests/integration -q  -> 81 passed
```

Up from Phase 2A's 146 (101 unit / 45 integration): +104 tests for
CLAUDE.md guards, changed-file detection and classification, the test
planner, the executor, the `changed`/`test` CLI commands, top-level
exception handling, and the hardened allow-secret adversarial cases.

## Quality gate commands run, in order, with results

> **Point-in-time record (2026-07-22).** The results below were accurate
> when measured at this checkpoint. Gate 3's clean audit later drifted:
> starting with `2955b25` (2026-07-22), subsequent checkpoints added
> secret-shaped test fixtures without the `forgeops:allow-secret` marker,
> and `forgeops audit` began exiting 2 with 20 blocked findings. Those
> findings were all synthetic test inputs — no real credential, no
> rotation, no history rewrite — and were annotated in the 2026-07-25
> Synthetic Secret-Fixture Hygiene checkpoint, which restored a clean
> audit (`58 informational, 12 pass, exit 0`). This section is preserved
> as written; see `.agent/DECISIONS.md` for the correction.

1. **Full test suite**: `python -m pytest tests -q` -> 250 passed.
2. **Syntax validation**: `python -m compileall -q forgeops tests` -> exit 0.
3. **`forgeops audit` against ForgeOps itself**: `34 informational, 12 pass (exit 0)` - clean, with every `forgeops:allow-secret` exemption in the test suite now visible as its own informational finding (34 of them), none silently absorbed.
4. **`forgeops status` against ForgeOps itself**: correct branch/HEAD/stack/instruction-file (`CLAUDE.md`) detection, exit 0.
5. **`forgeops changed` against disposable dirty repositories**: exercised directly in `tests/integration/test_cli_changed.py` (12 tests) and manually against ForgeOps's own dirty working tree during development - see "What validation actually caught" below for a real bug this surfaced.
6. **Targeted test planning against Python / Node / mixed React+FastAPI disposable examples**: all three run manually with `--plan` (see below) plus `tests/unit/test_planner.py` (22 tests) covering the rule set in isolation.
7. **Real targeted pytest run**: `forgeops test --targeted` executed against ForgeOps's own repo - real subprocess, real 250-test pytest run, `1 command(s) run, 1 succeeded (exit 0)`, log at `logs/test/<timestamp>/python-pytest.log`.
8. **Real targeted Node test run**: attempted against a disposable mixed-repo example (below) - `npm test` correctly *invoked* (after the Windows `.cmd`-shim fix) and correctly *failed* with `'vitest' is not recognized...` because no `npm install` had been run. This is the expected, correct outcome per gate 9, not a defect - no Node project with test dependencies already installed exists anywhere in scope (ForgeOps has no Node code; TrendForge is read-only and off-limits), so a *passing* real Node run could not be exercised this phase without installing dependencies, which was explicitly out of bounds.
9. **No Node dependency installation**: confirmed by inspection (no `npm install`/`pnpm install`/`yarn install` was ever run) and by `test_no_dependency_installation_ever_attempted`, which spies on every subprocess call the executor makes and asserts none of them is an install command.
10. **Human and JSON output validated**: for every new command, in both unit tests and the manual runs below.
11. **INTERNAL_ERROR behavior validated**: both via `tests/integration/test_exception_handling.py` (11 tests) and a manual CLI-level run (below).
12. **allow-secret restrictions validated**: via 8 new adversarial tests in `tests/unit/test_secret_scan.py` plus the dogfooded audit output in gate 3.
13. **Secret scan**: part of gate 3, clean.
14. **Generated/untracked file inspection**: `git status --short` reviewed by eye - every entry corresponds to a file this phase intentionally created or modified; no stray files, no `__pycache__`, no `.pytest_cache`.
15. **Logs remain ignored**: `git status --short --ignored | grep -i logs` -> `logs/audit/`, `logs/changed/`, `logs/doctor/`, `logs/status/`, `logs/test/` all reported `!!` (ignored) - confirms Phase 1's `.gitignore` rule covers the two new command log directories this phase added.
16. **`git diff --check`**: exit 0, no whitespace errors.
17. **TrendForge unchanged**: `git -C TrendForge status --short` empty; `git -C TrendForge rev-parse HEAD` -> `709fe58d7409c2c412715ebc0ec00cc0f68ea864`, identical to every prior phase. No ForgeOps command was ever pointed at TrendForge.
18. **State/docs updated**: this document plus `.agent/CURRENT_STATE.json`, `.agent/HANDOFF.md`, `.agent/DECISIONS.md`, `README.md`, `CHANGELOG.md` (see those files).

## Disposable-repository validation (gates 6-9, in detail)

Three scratch repos, built by hand outside any tracked directory:

**Python** (`pyproject.toml` + `[tool.pytest.ini_options]`, `calc.py`,
`test_calc.py`): changing `calc.py`'s implementation produced
`[targeted/medium] python -m pytest test_calc.py` - correct filename-
convention pairing.

**Node** (`package.json` with `scripts.test: "vitest run"` and a
`vitest` devDependency, `math.js`, `math.test.js`): changing `math.js`
produced `[targeted/medium] npm test math.test.js` - correct pairing and
correct use of the declared `scripts.test` command.

**Mixed React+FastAPI** (`backend/` with `requirements.txt`+`pytest.ini`,
`frontend/` with `package.json`+react+vitest, plus a
`shared/schemas/user_schema.py`): changing only the shared schema file
produced **two** broad commands - `pytest` from `backend/` (triggered via
the filename-shape `is_broad_impact` check, since `user_schema.py`
contains "schema") and `npm test` from `frontend/` (triggered via the
explicit cross-cutting-broadens-both-sides rule, since the Node side had
no changed files of its own to pair against) - exactly the rule 6
behavior the mission specified. Real execution: the backend pytest run
passed for real (`succeeded: true, returncode: 0`); the frontend npm run
correctly failed for real (`vitest' is not recognized...`, no
`node_modules` present) - overall exit code `COMMAND_EXECUTION_FAILURE`
(5), correctly not hidden or silently downgraded.

## What validation actually caught (not just "all green")

Five real defects were found and fixed this phase, every one of them by
dogfooding or real execution rather than by the unit suite alone:

1. **`git status --find-copies` is not a valid flag - git status has no
   copy-detection option at all** (only `git diff`/`git log` support
   `--find-copies`). The first version of `get_status_entries()` passed
   it anyway; git exited non-zero; the function's silent-failure-returns-
   empty-list pattern (copied from Phase 2A's `get_status`, which never
   hits this because its flags are valid) turned a broken git invocation
   into a completely silent "0 changes" result. Caught immediately by
   running `forgeops changed` against ForgeOps's own dirty working tree
   and noticing it reported zero changes when dozens of files were
   visibly modified. Fixed by removing the invalid flag; documented as an
   inherent git limitation (`ChangedFile.category` is never `"copied"`)
   in `docs/targeted-testing.md`.
2. **Untracked directories collapsed to one entry, and paths with spaces
   were quote-mangled.** The same fix (switching `get_status_entries()`
   to `-z` NUL-terminated parsing with `--untracked-files=all`) resolved
   both: `-z` output is never quoted/escaped, and `--untracked-files=all`
   expands new directories to individual files. Caught by
   `test_nested_working_directory` and `test_spacey_path_repo` in
   `tests/unit/test_changed.py`.
3. **`shared/schemas/user.py` classified as `backend`, not
   `shared-cross-cutting`.** `classify_area()`'s generic `name.endswith(".py")
   -> "backend"` fallback ran before the `shared/`/`common/`/`packages/`
   directory-prefix check ever got a chance. Caught by
   `test_classify_area_cross_cutting`. Fixed by reordering: directory-
   prefix checks (shared/frontend/backend) all run before any generic
   extension-based fallback.
4. **The test planner silently dropped coverage for every changed source
   file once *any* changed file was itself a test file.** `_plan_python`/
   `_plan_node` returned early on the first non-empty match instead of
   pairing every changed file independently and unioning the results -
   so a commit touching both `test_foo.py` and an unrelated `bar.py` with
   no test of its own would run only `test_foo.py`, silently missing
   `bar.py`'s impact entirely. This is exactly the failure mode rule 9
   ("never silently select zero tests when code changed") exists to
   prevent, just one level more subtle (never silently select *partial*
   coverage). Caught by running `forgeops test --targeted --plan` against
   ForgeOps's own real Phase 2B working-tree changes and noticing several
   genuinely-changed source files weren't reflected in the reasoning at
   all. Fixed by pairing per-file and broadening whenever any file in the
   batch has no discoverable pairing; `test_partial_pairing_broadens_rather_than_running_only_matched_subset`
   is the permanent regression test.
5. **`npm`/`npx`/`pnpm`/`yarn` are unreachable via
   `subprocess.run(["npm", ...], shell=False)` on Windows** - they're
   installed as `.cmd` shims, and Windows Python's subprocess module does
   not perform PATHEXT resolution the way an interactive shell does, so
   the call raised `FileNotFoundError` even with npm genuinely on PATH.
   This is exactly the class of bug gate 8 (a *real* Node execution
   attempt) exists to catch - no unit test using `sys.executable` could
   have found it, since `sys.executable` is always a real `.exe`. Fixed
   in `forgeops/core/subprocess_utils.py` by resolving `args[0]` through
   `shutil.which()` before invocation on Windows (which *does* perform
   PATHEXT resolution), while keeping `shell=False` everywhere and
   reporting the original, readable command in `ProcResult.args`.
   Regression test: `test_windows_cmd_shim_executable_is_resolved`.

Additionally, a design smell was found and fixed while writing the
exception-handling tests: `main()`'s command dispatch was a dict built
once at import time holding direct references to `doctor_cmd.run_doctor`
etc. - `monkeypatch.setattr("forgeops.cli.doctor.run_doctor", fake)`
silently had no effect, since the dict still held the original function
object. Fixed by resolving `getattr(module, f"run_{command}")` fresh on
every call. See `docs/cli-architecture.md`.

## Known limitations carried into Phase 2C

- No copy detection (`git status` has no such flag) - inherent, not a gap.
- Filename-convention test pairing only, not a real import graph.
- No Node test dependencies were available anywhere in scope to exercise
  a *passing* real Node execution this phase (see gate 8 above) - the
  planning and command-construction logic is thoroughly tested, and a
  real *failing* Node run was exercised end-to-end (including the
  Windows `.cmd`-shim fix), but a real *green* Node test run remains
  unverified until a Node project with installed dependencies is
  available.
- `forgeops test` has no `--full`/release-check mode yet - `--targeted`
  is the only implemented mode, per this phase's explicit scope.
- No linter/type-checker configured (carried over from Phase 2A).

## Exact recommended Phase 2C scope

Per the mission's explicit exclusion list for Phase 2B (release-check,
checkpoint, handoff, process cleanup, agents, worktrees, approvals, MCP
integrations, installers, Rocky interfaces) and the natural next
dependency order:

1. **`forgeops release-check`** - now has real building blocks to compose:
   `forgeops audit` (Phase 2A) + `forgeops test --targeted` broadened to
   a full/release mode (Phase 2B's planner already distinguishes
   "targeted" vs "broad" scope; a release-check would simply always
   request "broad" for every detected technology) + the existing
   exit-code table.
2. **`forgeops checkpoint` / `forgeops handoff`** - Phase 3 state-file
   *writers*, building on `forgeops/state/schema.py`'s validation
   (read-only so far) and the `.agent/CURRENT_STATE.json` schema already
   defined in `shared/schemas/current_state.schema.json`.
3. **`forgeops process-list` / `forgeops cleanup`** - net-new process
   registry, no dependency on this phase's work.
4. **`forgeops init`** - writes the `.agent/*` and `CLAUDE.md` template
   files (this phase's own `CLAUDE.md` is a natural template source).
5. **`forgeops worktree`, `forgeops agents`, `forgeops approvals`,
   `forgeops validate-config`, `forgeops install`/`uninstall`** remain
   gated behind Phase 3 state schemas and Phase 6/9/12 design work, as
   before.

Recommend Phase 2C open with `forgeops release-check`, since it is the
most direct payoff of Phase 2A+2B's work (audit + targeted testing) and
was explicitly named as this phase's natural successor in
`docs/phase2a-validation.md`'s own Phase 2B recommendation, now with a
real targeted-test planner to broaden from instead of a stub.
