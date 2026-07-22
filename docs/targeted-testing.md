# Targeted Testing

How `forgeops changed` classifies working-tree changes and how
`forgeops test --targeted` turns those changes into a test plan and
(optionally) executes it. Implemented in `forgeops/detectors/changed.py`,
`forgeops/testing/planner.py`, and `forgeops/testing/executor.py`.

## Changed-file semantics (`forgeops changed`)

Built from `git status --porcelain=v1 --find-renames --untracked-files=all -z`
(see `forgeops/core/git.py:get_status_entries`) - NUL-terminated so paths
containing spaces or unusual characters are never quote-mangled, and
`--untracked-files=all` so a brand-new directory is reported file-by-file
rather than collapsed to one entry (`get_ignored_summary()` deliberately
keeps the opposite, collapsed behavior for the separate ignored-files
summary, since that's a performance-motivated overview, not a precise
change list).

Each file becomes a `ChangedFile` with:
- `category`: `added | modified | deleted | renamed | untracked | conflicted`
  (never `copied` - see "Known limitations" below)
- `staged`: whether this specific entry is on the index side
- `area`: one of the 12 project-area classifications (frontend, backend,
  tests, configuration, dependencies, database, documentation,
  ci-deployment, infrastructure, security-authentication,
  shared-cross-cutting, unknown)
- `broad_impact`: whether this file's *shape* (not content) marks it as
  something that should broaden test scope - dependency manifests, build/
  test/CI config, anything path-containing "auth", schema/contract files,
  migrations, `.env.example`/`.env.template`, and root-level project config
- `technology`: a best-guess language/tool label from extension or filename

A single git status entry can produce **two** `ChangedFile` records (one
staged, one unstaged) when a file was partially staged and then further
modified - this is intentional, not a bug, so `forgeops changed --staged`
and `--unstaged` each see an accurate picture.

Classification is ordered, first-match-wins, and every rule is
directory-prefix or filename/extension based - never content-based and
never inferred from a bare directory name alone without corroborating
evidence (a directory called "backend" with no other signal classifies as
`unknown`, not `backend`).

## Test-planning rules (`forgeops test --targeted`)

Implemented per-changed-file, then unioned - not "stop at the first
matching rule for the whole batch." This distinction mattered in practice:
an earlier version short-circuited on the first changed file that was
itself a test file, silently ignoring every other changed source file's
own pairing needs (see `docs/phase2b-validation.md`).

1. **No changed files** -> a successful, empty plan. Not a warning, not
   an error - there's nothing to test.
2. **Any broad-impact file changed in a technology group** -> run that
   group's full suite. Never narrowed, regardless of what else changed.
3. **A changed file is itself a test file** -> pair with itself.
4. **A changed source file has a same-stem test file** discoverable by
   filename convention (`foo.py` -> `test_foo.py`/`foo_test.py`;
   `Component.jsx` -> `Component.test.jsx`/`Component.spec.jsx`) -> pair
   with it. Nearby-test search runs against every text file the repo's
   tree walk found (not just changed files), so a match can exist
   anywhere in the tree.
5. **Any changed file in the group has no discoverable pairing** -> the
   whole group broadens to its full suite. A *partial* set of pairings is
   never run alone - if even one changed file's impact is unaccounted
   for, running only the matched subset would under-test.
6. **A shared/cross-cutting or database/schema-shaped change in a mixed
   (Python+Node) repository** -> both technology groups broaden, even the
   side whose own files didn't change.
7. **No test-runner evidence for a group that has changed files** -> a
   warning naming exactly what evidence was missing (no pytest.ini/
   conftest.py/`[tool.pytest.ini_options]`/`pytest` in requirements.txt
   for Python; no `package.json` `scripts.test` and no jest/vitest
   devDependency for Node) - never an invented command.

### Command selection specifics

- **Python**: `[sys.executable, "-m", "pytest", ...]` - never a bare
  `pytest` (avoids depending on PATH resolution order across
  environments/interpreters).
- **Node**: prefers `package.json`'s own `scripts.test` (using the
  detected package manager - npm/pnpm/yarn - from the present lockfile);
  falls back to `npx vitest run` or `npx jest` directly if a
  `scripts.test` entry doesn't exist but the tool is a declared
  devDependency. Never invents a command when neither signal is present.
- **Working directory**: a targeted command with explicit file paths runs
  with `cwd` set to the technology group's own directory (e.g. `backend/`
  for `backend/requirements.txt`), with target paths relativized to that
  directory - not always the repo root - so subdirectory projects
  (`backend/`, `frontend/`) work the same as a root-level project. A
  broadened (whole-suite) command also runs from the group's directory,
  since a bare `pytest`/`npm test` invocation needs the correct working
  directory to discover the right tests at all.

## Execution policy (`forgeops test --targeted`, no `--plan`/`--dry-run`)

- Every selected command is **attempted regardless of earlier failures**
  - a partial run must never hide which specific commands passed or
    failed. There is no "stop on first failure" mode in Phase 2B.
- Bounded timeout (`DEFAULT_TIMEOUT_SECONDS = 600.0`, generous but finite)
  per command via the same `forgeops.core.subprocess_utils.run` used
  everywhere else in ForgeOps.
- Raw stdout/stderr for every command is redacted (`forgeops.security.redact`)
  and written to `logs/test/<timestamp>/<log_name>` - `python-pytest.log`,
  `node-test.log`.
- A command that never ran to completion (missing executable, timeout) is
  reported exactly as plainly as one that ran and failed - `ran` is
  always `True` once attempted; `succeeded` is the single source of truth
  for pass/fail, checked via `error`/`timed_out`/`returncode == 0`
  together, never inferred from just one of them.
- **No dependency installation is ever attempted.** The executor only
  ever runs the exact command the planner selected - never `pip install`,
  `npm install`, or any variant, under any circumstance. If a selected
  command fails because a dependency isn't installed (see
  `docs/phase2b-validation.md`'s real example), that failure is reported
  like any other - it is the operator's decision whether to install
  dependencies and retry, never ForgeOps's.

### Exit codes

- All selected commands ran and returned 0 -> `SUCCESS` (or
  `WARNINGS_PRESENT` if the plan separately reported a coverage gap).
- Any selected command failed to complete successfully (nonzero exit,
  timeout, or missing executable) -> `COMMAND_EXECUTION_FAILURE` (5) -
  chosen over inventing a new "tests failed" code, since 5's existing
  definition ("a required external command failed or was unavailable")
  already accurately describes a failing test run; see
  `docs/cli-exit-codes.md`'s stability guarantee.
- `--plan`/`--dry-run` never execute anything; their exit code reflects
  only the plan itself (`WARNINGS_PRESENT` if the plan has a coverage
  gap, `SUCCESS` otherwise).

## Known limitations

- **No copy detection.** `git status` has no `--find-copies` flag (only
  `git diff`/`git log` do) - `ChangedFile.category` is therefore never
  `"copied"` in this implementation. This was discovered by dogfooding:
  an early version passed `--find-copies` to `git status`, which is
  invalid and made git exit non-zero, which in turn made
  `get_status_entries()` silently return zero results - see
  `docs/phase2b-validation.md`.
- **Filename-convention pairing only**, not an import graph. A test file
  that doesn't follow `test_*`/`*_test.py`/`*.test.js`/`*.spec.js`
  naming, or that tests a module under an unrelated name, won't be found
  - the plan broadens instead of missing it silently, per rule 5 above.
- **Node command selection needs a real `package.json` to read.** If the
  file is present but malformed JSON, or the technology group's directory
  can't be determined from stack evidence, the group is treated as having
  no runnable command (a warning, not a crash).
- **Windows `.cmd` shims** (npm, npx, pnpm, yarn) are resolved via
  `shutil.which()` before invocation (see
  `forgeops/core/subprocess_utils.py:_resolve_executable`) - discovered
  by real execution during Phase 2B validation, where `subprocess.run(["npm",
  ...], shell=False)` reported "executable not found" even with npm
  genuinely on PATH, because Windows Python's subprocess module doesn't
  perform PATHEXT resolution the way an interactive shell does.
