# Decisions

Running log of architecture/process decisions. Newest entries go at the
bottom. Each entry: `## YYYY-MM-DD — <decision title>` followed by 2-3
lines of context/why. This is a history, not a living doc — don't edit
past entries, append new ones.

## 2026-07-21 — ForgeOps directory recovered from a stray TrendForge copy

At session start, `C:\Users\joshd\ForgeOps` was found to contain a
byte-for-byte duplicate of the TrendForge working tree (including live
`.env` files, a database, and uploads) sitting inside a fresh, empty git
repo (branch `master`, 0 commits) — not the clean scaffold the mission
expected. Flagged to the operator rather than guessed at, given the
presence of real secrets on disk and the scale of the discrepancy. The
operator confirmed this was the wrong path and cleaned the directory out
externally before work resumed. Lesson: when the active repo's actual
contents contradict the stated mission premise, stop and confirm before
taking structural or destructive action, even under an otherwise
autonomous mandate.

## 2026-07-21 — ForgeOps supersedes TrendForge's prior "stay minimal" decision

TrendForge's own `.agent/DECISIONS.md` records an explicit operator
decision to keep ForgeOps-style tooling project-local and minimal
("stays project-local tonight, no generic platform"; "packaging kept as
an index, not a restructured kit"), deferring the full multi-project
toolkit. This mission explicitly supersedes that scope decision — the
current priority is the generalized, installable-elsewhere toolkit itself.
The proven 3-script/2-skill/`.agent`-convention core is kept as the
literal starting point; the decision to stop expanding it is what's
discarded. Full reasoning: `docs/architecture-decision.md`.

## 2026-07-21 — Repo-local git identity, not global

The first commit failed with "Author identity unknown" — no global
`.gitconfig` exists on this machine. Rather than run `git config
--global` (against the standing git-safety-protocol instruction not to
touch git config), set `user.name`/`user.email` locally in this repo
only, mirroring TrendForge's own repo-local (not global) identity
convention discovered by reading its local config. Non-destructive,
scoped to this repo, and matches existing practice rather than
introducing a new one.

## 2026-07-21 — Phase 1 placeholders state their implementing phase, never silently no-op

Every structural file created in Phase 1 that isn't yet functionally
complete (installer scripts, the `forgeops` CLI entry point) prints a
clear "not yet implemented, see Phase N" message and exits non-zero,
rather than exiting 0 or doing nothing silently. Chosen so that a future
session (or the operator) running one of these by mistake gets an honest
signal instead of a false "it worked" or a silent failure.

## 2026-07-22 — Dogfooding is mandatory validation, not optional polish

Phase 2A's `forgeops audit`, run against ForgeOps's own repo, initially
reported `forgeops/security/secret_scan.py` as a dangerous file (a
self-referential false positive: code that implements secret scanning
matches a filename heuristic meant to catch files that contain one).
Separately, a disposable repo shaped like a real mixed React/FastAPI
project (manifests in `backend/`/`frontend/` subdirectories, not the
fixture root) revealed that `detect_stack` was silently missing every
manifest not at the repository root - invisible to the unit suite because
every unit-test fixture happened to place files at the fixture root.
Both were real, user-facing defects that 146 passing unit/integration
tests did not catch on their own. Lesson, recorded here rather than only
in `docs/phase2a-validation.md` because it should shape how every future
phase validates itself: running the tool against itself and against at
least one repo shaped like a real project is not a nice-to-have on top of
a green test suite - it is where the test suite's own blind spots (fixture
files conveniently placed at a repo root, no self-referential file names)
get found. Both fixes shipped with permanent regression tests
(`test_manifests_in_subdirectories_are_detected`,
`test_source_file_implementing_secret_scanning_is_not_flagged`) so the
specific gaps can't silently reopen.

## 2026-07-22 — Inline secret-scanner allowlist marker (`forgeops:allow-secret`)

ForgeOps's own test suite needs to contain fake, secret-shaped strings
(a fake AWS key, a fake JWT) to test `forgeops/security/secret_scan.py`
itself - without an escape hatch, `forgeops audit` run against ForgeOps's
own repo would permanently report itself as blocked. Added a literal
substring marker, `forgeops:allow-secret`, that suppresses scanning for
exactly the line it appears on (not the whole file, not a directory).
Deliberately narrow and explicit - a human has to write the marker on the
specific line, so it can't accidentally suppress an unrelated real
finding elsewhere in the same file. Documented in
`docs/audit-security-model.md` as part of the audit's security contract,
not hidden as an implementation detail, since anyone auditing a project
that adopts ForgeOps needs to know this opt-out exists.

## 2026-07-22 — `forgeops:allow-secret` hardened to be path-scoped, not global

The Phase 2A marker (previous entry) suppressed scanning for *any* line
containing it, anywhere in the repo - a genuine gap: production source
code could self-declare an exemption for a real leaked credential. The
Phase 2B mission explicitly required closing this. Redesigned so the
marker only applies inside approved zones (`tests/`, `fixtures/`,
`examples/`, common test-naming conventions, plus a project-configurable
`allow_secret_paths` list) - outside those zones the marker is inert, and
`.env`/database/browser-state files can never be exempted regardless,
via an independent categorical-exclusion check inside `secret_scan.py`
itself (not just relying on the caller's file classification). Every
granted exemption is now returned and surfaced as a visible
`informational` audit finding rather than silently vanishing. Full
design and adversarial tests: `docs/audit-security-model.md`,
`tests/unit/test_secret_scan.py`.

## 2026-07-22 — Dispatch by fresh attribute lookup, not a dict of bound references

While writing Phase 2B's exception-handling tests, `monkeypatch.setattr("forgeops.cli.doctor.run_doctor",
fake)` had no effect on `forgeops.cli.main()`'s behavior - `main()`'s
command dispatch was a module-level dict (`_SIMPLE_DISPATCH`) built once
at import time, holding the original `doctor_cmd.run_doctor` function
object directly. Patching the module attribute afterward doesn't change
what the dict already captured. Fixed by resolving `getattr(module,
f"run_{command}")` fresh on every call in `forgeops/cli/__init__.py`.
Lesson for future CLI dispatch code in this project: prefer resolving
callables by attribute lookup at call time over caching them in a
module-level structure, specifically because it keeps the dispatch
naturally testable via monkeypatch without special-casing.

## 2026-07-22 — Real execution and disposable multi-stack repos remain mandatory, not just dogfooding-on-ForgeOps-itself

Phase 2B's targeted-test planner and executor passed 250 unit/integration
tests before a single real disposable-repo validation run. That run
still found two defects unit tests couldn't have caught: `git status
--find-copies` isn't a real flag (git status has no copy-detection option
at all) and was silently making changed-file detection report zero
changes on a visibly dirty repo; and `npm`/`.cmd`-shim executables are
unreachable via `subprocess.run([...], shell=False)` on Windows without
an explicit `shutil.which()` resolution step, invisible to any test using
`sys.executable` (always a real `.exe`). Extends the Phase 2A lesson
above: dogfooding against ForgeOps's own repo is necessary but not
sufficient - a repo shaped like a real, unfamiliar project (here: a
disposable mixed React+FastAPI example) and a real subprocess execution
against a real tool (here: `npm`) are what surfaced these two. Both fixes
shipped with permanent regression tests. Full account:
`docs/phase2b-validation.md`.
