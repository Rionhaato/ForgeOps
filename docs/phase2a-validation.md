# Phase 2A Validation

## Test totals

```
python -m pytest tests -q
146 passed in ~14s

python -m pytest tests/unit -q         -> 101 passed
python -m pytest tests/integration -q  -> 45 passed
```

`tests/integration/test_cli_entrypoint.py` exercises the real `python -m
forgeops` subprocess entry point (not just the underlying Python
functions); the rest of `tests/integration/` calls `run_doctor` /
`run_status` / `run_audit` directly for speed, which is still a true
integration test of the CLI/core/detectors/security stack together — only
the OS-process boundary itself is skipped for those.

## Quality gate commands run, in order, with results

1. **Full test suite**: `python -m pytest tests -q` -> 146 passed (0 failed).
2. **Syntax validation**: `python -m compileall -q forgeops tests` -> exit 0 (no configured linter/type-checker yet - noted as a gap below, not silently skipped).
3. **Secret scanner against ForgeOps itself**: `forgeops audit` from the repo root -> `3 informational, 13 pass (exit 0)`. This required two real fixes surfaced by dogfooding, not just running the command once green - see "What validation actually caught" below.
4. **Untracked/generated file inspection**: `git status --short` reviewed by eye; every untracked path corresponds to a file this phase intentionally created (37 files: `forgeops/` modules, `tests/`, `shared/schemas/current_state.schema.json`) - no stray files, no `__pycache__`, no `.pytest_cache` (all correctly gitignored from Phase 1).
5. **Logs ignored**: `git status --short --ignored | grep -i logs` -> `logs/audit/`, `logs/doctor/`, `logs/status/` all reported `!!` (ignored), confirming Phase 1's `.gitignore` rule (`logs/*` + `!logs/.gitkeep`) covers the directories these commands actually create.
6. **TrendForge unchanged**: `git -C C:\Users\joshd\TrendForge status --short` -> empty (clean); `git -C C:\Users\joshd\TrendForge rev-parse HEAD` -> `709fe58d7409c2c412715ebc0ec00cc0f68ea864`, identical to the value recorded at the start of Phase 0. No ForgeOps command was ever pointed at TrendForge with `--repo` (doing so would write into TrendForge's `logs/`, which is why the disposable-repo validation below uses a scratch directory instead).
7. **`forgeops doctor` / `status` / `audit` against a disposable example repo**: see below.
8. **Human-readable and JSON modes**: both exercised for every command in step 7 and throughout `tests/integration/`.
9. **`git diff --check`**: `git add -A && git diff --cached --check` -> exit 0, no whitespace errors.
10. **State and handoff updated**: `.agent/CURRENT_STATE.json`, `.agent/HANDOFF.md`, `.agent/DECISIONS.md` updated as part of this commit (see those files).

## Disposable-repository validation (step 7, in detail)

Built by hand outside any tracked directory (a scratch temp path, not
`examples/` - `examples/python|node|react-fastapi/` remain empty
placeholders for Phase 13's fixture-repo tooling, per Phase 1): a fresh
`git init`, `backend/requirements.txt` containing `fastapi`,
`frontend/package.json` with `react` + `vite` dependencies, a real-looking
`backend/.env`, a tracked `backend/.env.example`, and
`backend/oops.py` containing a fake AWS-key-shaped string.

- `forgeops doctor --repo <path>` -> `7 pass, 2 warning, 1 informational
  (exit 1)`. The two warnings are `claude`/`codex` not being on PATH
  (correct - see `.agent/PROJECT_FACTS.md`), confirming "missing optional
  tools should produce warnings, not false failures."
- `forgeops status` -> correctly reported `master`, the real HEAD, 0
  staged, 0 modified, and (before the fix below) an incomplete stack list.
- `forgeops audit` -> correctly `BLOCKED` (exit 2) on two independent
  findings: `backend/.env` matching the dangerous-filename pattern, and
  the fake AWS key in `backend/oops.py` matching the secret-pattern scan.
  `backend/.env.example` was correctly reported informationally and never
  content-scanned; `backend/.env`'s contents were never read (only its
  path and category appear anywhere in the output).

## What validation actually caught (not just "all green")

Two real defects were found and fixed during this phase specifically
*because* dogfooding and disposable-repo validation were run, not
skipped in favor of unit tests alone:

1. **Stack detection silently missed manifests in subdirectories.**
   `forgeops status` against the disposable repo above initially reported
   only `python(low)` — missing `fastapi`, `node`, `react`, and `vite`
   entirely, even though `backend/requirements.txt` and
   `frontend/package.json` were both present and correctly found by the
   tree walk. Root cause: `forgeops/detectors/stack.py` compared
   `tree.dependency_manifests` (full relative paths like
   `backend/requirements.txt`) against bare filenames (`"requirements.txt"
   in manifests`), which only worked for root-level files — exactly the
   uncommon case, since a real mixed React/FastAPI repo almost always has
   manifests in subdirectories. Every unit test for `detect_stack` placed
   its fixture files at the fixture root, so the bug was invisible to the
   unit suite; only a disposable repo shaped like a real project surfaced
   it. Fixed by indexing manifests/markers by basename
   (`forgeops/detectors/stack.py:_by_basename`) and added
   `test_manifests_in_subdirectories_are_detected` as a permanent
   regression test. Re-running `forgeops status` against the same
   disposable repo after the fix correctly reported all seven stack
   findings (`python`, `fastapi`, `node`, `package-manager` (ambiguous, no
   lockfile), `react`, `vite`, `mixed-python-node`).
2. **`forgeops audit` flagged its own security module as a dangerous
   file.** Running `forgeops audit` against the ForgeOps repo itself
   reported `forgeops/security/secret_scan.py` as blocked (matching the
   `*secret*` filename pattern) — a self-referential false positive: a
   module that *implements* secret scanning matches a heuristic meant to
   catch files that *contain* a secret. Fixed by excluding common
   source-code extensions from the name-shape-only patterns
   (`*credentials*`/`*secret*`) in
   `forgeops/security/dangerous_files.py`, and separately, the same
   dogfooding run showed the test suite's own fake-secret fixture strings
   (needed to test the scanner) tripping the content-based scanner —
   fixed by adding the `forgeops:allow-secret` inline marker to
   `secret_scan.py` and annotating the specific fixture lines. Both fixes
   are documented in detail in `docs/phase2a-porting-notes.md` and
   `docs/audit-security-model.md`; regression tests exist for both
   (`test_source_file_implementing_secret_scanning_is_not_flagged`,
   `test_allowlist_marker_suppresses_the_line`).

An encoding issue was also caught this way: an em dash (`—`) in one
`audit` label garbled on this Windows console (cp1252, not UTF-8).
Fixed by removing all non-ASCII characters from anything the CLI prints
(`grep -P "[^\x00-\x7F]" forgeops -r` now returns nothing) rather than
attempting to force UTF-8 console mode, which isn't guaranteed available
on every terminal ForgeOps might run in.

## Known limitations carried into Phase 2B

- No linter/type-checker is configured yet (`ruff`/`mypy` are not in
  `pyproject.toml`'s dev dependencies) - `compileall` catches syntax
  errors only, not type errors or style issues.
- No top-level exception handler in `forgeops/cli/__init__.py:main()` -
  see `docs/cli-architecture.md` "Known limitations."
- `examples/python|node|react-fastapi/` remain empty placeholders; Phase
  2A's disposable-repo validation used an ad-hoc scratch repo instead of
  a checked-in fixture, since building the Phase 13 fixture-repo tooling
  is out of scope for this phase.

## Exact recommended Phase 2B scope

Per the mission brief's command list, in a sensible dependency order:
1. `forgeops changed` (changed-file detection) - needed by `test
   --targeted` next.
2. `forgeops test --targeted` / `forgeops test --full` (Phase 4's
   targeted-testing logic, stack-aware using `forgeops/detectors/stack.py`
   already built here).
3. `forgeops release-check` (composes `audit` + `test --full` + the
   exit-code table already defined).
4. `forgeops checkpoint` / `forgeops handoff` (Phase 3 state-file writers,
   building on `forgeops/state/schema.py`'s validation already built here).
5. `forgeops process-list` / `forgeops cleanup` (process registry, net-new).
6. `forgeops init` (writes the `.agent/*` template files, using the
   templates named in `docs/reusable-components.md`).
7. `forgeops worktree`, `forgeops agents`, `forgeops approvals`,
   `forgeops validate-config`, `forgeops install`/`uninstall` remain
   later still, gated on Phase 3 state schemas and Phase 6/9/12 design
   work landing first.

Every one of these is a **mutation-capable** command (writes state files,
runs test suites, manages processes/worktrees) - Phase 2A deliberately
proved the read-only foundation first. Recommend Phase 2B open with
`forgeops changed` + `forgeops test --targeted` specifically, since
they're the highest-leverage commands for the "reduce repeated repository
investigation" and "select tests based on changed files" goals named
first in the mission's primary-mission list, and they build directly on
`forgeops/core/git.py`'s `get_status` (already returns per-file paths)
and `forgeops/detectors/stack.py` (already maps a repo to its stack) with
no new Phase-3-sized state-schema dependency blocking the start.
