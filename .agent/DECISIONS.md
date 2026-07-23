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

## 2026-07-22 — release-check aggregates existing run_* functions, never re-derives their checks

`forgeops release-check`'s mission was explicit: "aggregate existing
deterministic checks instead of duplicating their implementations."
Implemented literally - `run_release_check` calls `run_doctor(...)`,
`run_audit(...)`, and `run_full_test(...)` directly (the same functions
their own CLI commands call), and folds each `CommandResult.checks` list
into its own, prefixed by source (`doctor.*`, `audit.*`, `test.*`) so
provenance stays traceable. This is a deliberate architectural choice,
not just an implementation shortcut: it means release-check's own
surface area for new bugs is small (three new gates: working-tree
cleanliness, branch/HEAD availability, dependency-free `compileall`
validation), and any future fix to doctor's or audit's own checks
automatically improves release-check too, with nothing to keep in sync.

## 2026-07-22 — exit_codes.worst() precedence nuance surfaced by its first real caller

`exit_codes.worst()` was defined in Phase 2A (`forgeops/core/exit_codes.py`)
but had zero real callers until `release-check` became the first one,
using it to combine its own gates' worst exit code with `run_doctor`'s,
`run_audit`'s, and `run_full_test`'s. Its documented precedence ranks
`COMMAND_EXECUTION_FAILURE` (5) above `BLOCKED` (2) - so a repository with
both a leaked secret (blocked) and a failing test suite (command
execution failure) in the same run reports exit code 5, not 2. This
surfaced immediately while writing the first adversarial test for this
exact scenario (a fixture repo with a secret but no real test file,
where pytest's own "no tests collected" exit 5 out-ranked the expected
`BLOCKED` result). Deliberately not changed - the precedence is
pre-existing Phase 2A architecture, and this bounded Phase 2C checkpoint
was explicitly scoped to implement `test --full` and `release-check`,
not redesign exit-code precedence. Instead: documented explicitly in
`docs/release-check.md` and `docs/cli-exit-codes.md`, and
`data.blocking_checks` in release-check's JSON output always lists every
blocking finding regardless of which one the single exit-code integer
reflects, so nothing is actually hidden from a consumer that reads past
the bare exit code.

## 2026-07-22 — Commit requires explicit authorization, not the standing "commit after gates pass" policy

Phase 2A and Phase 2B both closed with a commit as part of their own
standing instructions ("create one coherent commit only after every
required gate passes"). This phase's instructions were different and
more restrictive: "Do not create a commit unless Joshua explicitly
authorizes it." Followed literally - all validation gates for Phase 2C
passed (300 tests, compileall clean, TrendForge unchanged, no remote, git
diff --check clean), but no commit was made. `.agent/HANDOFF.md` records
the exact commit message to use once authorization is given. Lesson:
each checkpoint's own instructions on commit policy take precedence over
the pattern established by prior checkpoints - don't assume the same
commit-at-the-end behavior carries forward without re-checking.

## 2026-07-22 — checkpoint/handoff preserve narrative, recompute objective facts

`forgeops checkpoint` writes `.agent/CURRENT_STATE.json` and `forgeops
handoff` writes `.agent/HANDOFF.md` - both the *existing* canonical
locations, not new parallel files, per this checkpoint's explicit
instruction to reuse the existing state schema and files where
practical. The core design choice: fields a deterministic script can
honestly derive from git/the filesystem (branch, HEAD, working-tree
shape, detected stack, remote presence) are recomputed fresh on every
call; fields that are inherently narrative (mission, completed_work,
blockers, next_action, last_checkpoint.phase) are carried forward
unchanged from whatever `CURRENT_STATE.json` already existed, never
reinvented or guessed at. No deterministic script can honestly answer
"what phase of the mission is this" from git state alone - trying to
would have meant either inventing plausible-sounding prose (violates
"never invoke model reasoning" for these commands) or discarding real
continuity information every time checkpoint runs. If no previous
document exists, narrative fields start empty rather than fabricated.

## 2026-07-22 — checkpoint and handoff each write exactly one file; no dual-file transaction

Considered making one combined operation write both `CURRENT_STATE.json`
and `HANDOFF.md` atomically together (transaction-like, with rollback).
Rejected: there is no code path where a single command invocation needs
to write both files, so building cross-file transaction machinery would
have been unused complexity. `forgeops handoff` instead *reads* whatever
`CURRENT_STATE.json` currently exists (computing a fresh snapshot via
the same shared `build_checkpoint_data()` if none does) and writes only
`HANDOFF.md`. Each command's own single-file write is atomic
(temp-file-then-`os.replace()`, see `forgeops/state/atomic_write.py`);
running both commands back to back is two independently-safe atomic
operations, not one transaction. Documented explicitly in
`docs/checkpoint-and-handoff.md` so a future session doesn't assume
partial-failure-across-both-files is a real failure mode.

## 2026-07-22 — unsupported CURRENT_STATE.json schema_version is a warning, never a block

If an existing `.agent/CURRENT_STATE.json` has a `schema_version` this
version of forgeops doesn't recognize (checked against the single
source of truth, `forgeops/state/schema.py:SUPPORTED_SCHEMA_VERSIONS`),
both `checkpoint` and `handoff` report it as a `warning`-status check
and reset narrative fields to empty defaults rather than attempting to
interpret an unknown future document shape - then still write a fresh,
valid `schema_version: 1` document. Chosen over refusing to run
entirely: a session that needs to checkpoint/hand off should still be
able to, even from a repository whose state file was last touched by a
newer version of forgeops; losing that one run's narrative continuity
(visible and explained via the warning) is preferable to blocking
entirely.

## 2026-07-22 — approval boundaries and standard validation commands mirrored as constants, not re-derived from CLAUDE.md

`forgeops/cli/handoff.py` hardcodes `STANDARD_VALIDATION_COMMANDS` and
`APPROVAL_BOUNDARY_CATEGORIES` as small constants mirroring `CLAUDE.md`
sections 8 and 11, rather than parsing `CLAUDE.md`'s prose/fenced code
block at runtime. Parsing would have been more DRY but fragile (a
heading rename or reformatted code fence silently breaks extraction with
no test able to catch the mismatch until a real handoff document came
out wrong). The constants are explicitly commented as mirroring specific
`CLAUDE.md` sections so a future edit to either place has a fighting
chance of updating the other; a future phase could add a regression test
that fails if `CLAUDE.md`'s section 8 code fence and this constant tuple
diverge, but that wasn't built now (out of scope for this bounded
checkpoint) - noted here as a known gap, not hidden.

## 2026-07-22 — cleanup_eligible requires an exact registry match; heuristic association is never sufficient

`forgeops cleanup` will only ever attempt to terminate a process
classified `managed` by `process_association.classify_process()` - an
exact `.agent/runtime/PROCESS_REGISTRY.json` record match on PID,
repository, and start time, with an allowed category. `associated`
(heuristic: the command line, its own or its parent's, merely contains
the repository path) is deliberately never cleanup-eligible, no matter
how many separate pieces of heuristic evidence accumulate. Considered
letting high-confidence `associated` processes (e.g. two independent
matching signals) become eligible too, to make cleanup more useful
without a registry populated yet - rejected: the mission's own
instruction was explicit ("do not classify a process as managed solely
because its executable name is common... use explicit evidence") and,
more importantly, a command line containing a repository path proves
much less than an actual PID+start-time-matched registry record does -
it's exactly the kind of coincidental match that could catch an
unrelated user's own editor session or terminal tab open in the same
directory. `cleanup_eligible` is `False` on every `AssociationResult`
except `managed`, enforced by construction, not by a runtime check
elsewhere that could be bypassed.

## 2026-07-22 — no force-kill path exists, by deliberate choice, and this was validated for real

The mission's own instructions said implementing a force flag "is not
required" and "prefer not to implement force termination." Taken
literally: `forgeops cleanup --execute` only ever runs
`taskkill /PID <pid>` (no `/F`), waits a bounded time, and reports
failure - never escalating - if the process is still running. This was
validated against a **real** disposable test process (a plain
`python -c "import time; time.sleep(300)"` child, registered with a real
PID + real start time under a disposable temp repository, never
TrendForge or anything real): graceful `taskkill` did not stop it within
the timeout on this machine, and cleanup correctly reported that failure
without escalating. This is the intended, safe behavior, not a gap to
patch - Windows console applications frequently don't respond to a
non-forceful termination request the way GUI applications with a message
loop can, and accepting "sometimes graceful termination just doesn't
work" is the tradeoff for never force-killing anything ForgeOps didn't
launch itself. The disposable test process was force-killed manually,
outside of `forgeops cleanup`, purely as validation-session cleanup.

## 2026-07-22 — a process whose command line can't be read is `unrelated`, not `uncertain`

Real validation against the live ForgeOps development machine initially
classified 137 of 300 scanned processes as `uncertain` - nearly all
ordinary Windows system processes (`svchost.exe`, `csrss.exe`, ...)
whose command line simply can't be read when running unelevated. This
was technically "safe" (never wrongly treated as associated/managed) but
made `process-list`'s output useless - burying any real signal in noise.
Fixed by reserving `uncertain` for a *specific* reason to be cautious (a
registry record that exists but doesn't fully check out - wrong
repository, disallowed category) rather than "any process we couldn't
positively rule out." A process with zero evidence either way,
regardless of *why* there's no evidence, is `unrelated` and isn't
reported - it was never going to be a cleanup candidate regardless, so
omitting it from the report has no safety cost. Documented explicitly in
`docs/process-list-and-cleanup.md` since it's a subtle distinction a
future reader could easily get backwards.

## 2026-07-22 — `Get-CimInstance`'s CreationDate is a .NET JSON date, not a raw WMI datetime string (real bug, caught by real validation)

The original `_parse_wmi_datetime()` only recognized the raw WMI
`CIM_DATETIME` string form (`20260722153045.123456-300`). Unit tests
written against that same wrong assumption all passed - the bug was only
caught when a **real** disposable test process's registry record showed
`start_time_utc: None` despite the process genuinely existing, during
the manual real-process cleanup validation. `Get-CimInstance` (the
modern CIM cmdlet, used here instead of the older `Get-WmiObject`)
auto-converts WMI's raw datetime into a .NET `DateTime`, and
`ConvertTo-Json` renders *that* as the legacy `/Date(<epoch-ms>)/`
convention. Fixed to parse both forms, with a regression test for each -
this is exactly the kind of gap real dogfooding surfaces that a unit
test written against an assumption, rather than a real command's real
output, cannot catch on its own (consistent with the Phase 2A/2B lesson
already recorded above: "real execution and disposable multi-stack repos
remain mandatory, not just dogfooding").

## 2026-07-23 — Context-Efficiency Foundation: ForgeOps remains the workflow authority

This checkpoint added `forgeops resume-context`, project-local skills, a
read-only subagent, and two hooks specifically to reduce main-session
context consumption - not to hand any of ForgeOps's governance to
another framework or tool. `CLAUDE.md` remains the sole always-on rule
source; skills are explicitly-invoked procedure references that point
back at `CLAUDE.md` rather than restating or superseding it; the
subagent and hooks enforce existing rules (TrendForge protection,
destructive-git rejection, no-force-kill, dry-run defaults) rather than
introducing new ones. Full governance precedence recorded in
`docs/superpowers-compatibility.md` and `docs/context-efficiency.md`.
"Context reduction" is scoped explicitly to mean less main-conversation
context, never a claim that total system token usage anywhere dropped
to zero.

## 2026-07-23 — Superpowers is prior art, evaluated but never installed as governance

Superpowers (`github.com/obra/superpowers`) was researched via public
documentation only (no clone, install, or execution) specifically to
harvest useful workflow patterns without adopting its coordinator role.
Three of its thirteen evaluated areas were rejected outright
(subagent-driven development with write access, automatic commits, and
large `SessionStart` injection) because they directly contradict
standing ForgeOps rules; MCP-related guidance was rejected regardless of
whether Superpowers's own core avoids MCP, since ForgeOps's "no MCP"
rule is unconditional. See `docs/superpowers-compatibility.md` for the
full adopt/adapt/reject matrix and the explicit governance-precedence
ordering it records.

## 2026-07-23 — `resume-context` avoids `build_checkpoint_data`'s tree scan by design

`forgeops resume-context` intentionally does not reuse
`forgeops.state.checkpoint.build_checkpoint_data` even though that
function already assembles most of the same narrative fields - calling
it would trigger a full repository tree scan (`detect_stack`) on every
invocation, which defeats the entire purpose of a command meant to be
near-free to run at the start of a session. Instead `resume-context`
reads only the already-computed narrative fields out of the existing
`CURRENT_STATE.json` (via the proven `load_previous_state`) and combines
them with cheap, direct git calls. The tradeoff accepted: `resume-context`
can only ever be as fresh as the last `forgeops checkpoint` run for
narrative fields (mission, blockers, next_action) - acceptable, since
those fields are narrative and only `forgeops checkpoint`/`handoff`
ever legitimately update them anyway.

## 2026-07-23 — compact-document size ceiling enforced by fixed per-field caps, not a runtime shrink loop

`RESUME_CONTEXT_MAX_BYTES` (4096) is enforced by choosing small, fixed
caps per field (e.g. 3 blockers at 120 chars each, 3 files per group at
70 chars) whose worst-case sum is comfortably under the ceiling, rather
than a build-then-measure-then-shrink retry loop. Considered the retry
loop as more "adaptive," but rejected: fixed caps are simpler to reason
about, trivially testable with one adversarial-narrative fixture
(`tests/unit/test_resume_context.py::test_bounded_output_size_under_adversarial_narrative`),
and avoid a whole class of bugs where a shrink pass could still overshoot
if a future field is added without updating the shrink logic.

## 2026-07-23 — the recovery-reviewer subagent gets Read/Grep/Glob only, never Bash

`.claude/agents/forgeops-recovery-reviewer.md` deliberately omits `Bash`
even though that means it cannot run `git` itself. Considered granting
Bash restricted to read-only git subcommands - rejected, because
Claude Code's subagent `tools:` frontmatter grants or denies whole tools,
not specific command patterns within a tool; any `Bash` grant would be a
real path to mutation, contradicting "never commit / never terminate a
process / never modify TrendForge" for a subagent explicitly designed to
be safe to run unsupervised in its own context. The invoking agent is
instead expected to pass relevant git/`resume-context` facts directly in
the prompt; the subagent can still independently read `.git/HEAD` and
`.git/refs/heads/*` as plain text via `Read` if it needs to corroborate
branch/HEAD itself.

## 2026-07-23 — session-end handoff hook targets `SessionEnd`, not `Stop`

Phase F's brief named the hook "Stop/session-end handoff hook," but
Claude Code's `Stop` event fires after every single assistant turn, not
once at session end - wiring a handoff write to `Stop` would run it far
more often than "session-end" implies and was rejected. `SessionEnd`
(fired once for `clear`/`resume`/`logout`/`prompt_input_exit`/other exit
reasons) is the correct event for a one-time end-of-session write, and
is what `.claude/settings.json` actually wires
`.claude/hooks/sessionend_handoff.py` to. Documented here since it's a
deliberate deviation from the literal phrase in the checkpoint brief,
not an oversight.

## 2026-07-23 — `forgeops init` resolves its target directly, never by walking up for `.git`

Every other ForgeOps command discovers its repository root via
`resolve_repo_root` (walk upward from cwd/`--repo` looking for `.git`).
`forgeops init` deliberately does not: its whole purpose is to safely
handle a directory that isn't a git repository yet, and if it walked
upward it could silently initialize a different (parent) repository
than the one explicitly named on the command line - the opposite of
"safe and deterministic." The target is exactly the given `PATH`
argument, or cwd if omitted; a directory nested one level below an
already-governed parent gets its own independent `.agent/` structure,
verified by `test_does_not_traverse_into_parent_or_child_repository`.
This is also why `init` takes a positional `PATH` instead of the
`--repo <path>` flag every other command uses - see `docs/project-init.md`.

## 2026-07-23 — `forgeops init` treats a malformed `CURRENT_STATE.json` as a blocking conflict, not a warning

`forgeops checkpoint`/`handoff` treat an existing `.agent/CURRENT_STATE.json`
with an unsupported/malformed `schema_version` as a `warning` and
silently reset it to fresh narrative defaults (see the 2026-07-22 entry
above) - the right choice for a command whose whole job is producing a
fresh snapshot on every call. `forgeops init` reuses the same
compatibility check but treats the identical condition as a blocking
`conflict` instead, refusing to write anything. Deliberately more
conservative: `init` is a first-time bootstrap operation a caller might
run against a project with real, unusual pre-existing state, and
silently discarding that state (even "for a good reason") is a worse
failure mode for a command explicitly required to "never overwrite
malformed or unknown `.agent` state." The two commands' differing
defaults for the same underlying signal are intentional, not an
inconsistency - documented explicitly in `docs/project-init.md` so a
future reader doesn't try to "fix" one to match the other.

## 2026-07-23 — `forgeops init` does not pre-create `.agent/runtime/`, `.agent/checkpoints/`, or `.agent/logs/`

The checkpoint brief's own "preferred managed paths" list named all
three, but also explicitly instructed against creating "empty
speculative directories... unless the current runtime/state writers
already require them." Checked: `forgeops.state.runtime_registry` (the
only current writer that would ever use `.agent/runtime/`) already
creates its own parent directory via `atomic_write_text` the moment it
first writes a registry record; nothing in this repository references
`.agent/checkpoints/` or `.agent/logs/` at all. Only `.agent` itself is
pre-created (it doubles as a conflict-detection point - a plain file
named `.agent` must block init). Chosen over creating all three "to
match the brief's list literally," since empty, currently-unused
directories are exactly the kind of speculative scaffolding the more
specific instruction overrode. If a future command starts requiring one
of these ahead of time, it should create it itself (mirroring how
`runtime_registry` already behaves), not have `init` guess at a need
that doesn't exist yet.

## 2026-07-23 — TrendForge protection duplicated as a second, independent layer in `forgeops init` rather than unified with the PreToolUse hook

`.claude/hooks/pretooluse_safety.py` already blocks any Claude Code tool
call referencing the TrendForge path. `forgeops/core/paths.py` now also
hardcodes `READONLY_REFERENCE_REPO` and
`is_protected_reference_path()`, checked directly inside `run_init`
before any filesystem existence check. Deliberately not unified into one
shared source of truth: the hook protects *this Claude Code session's*
tool calls; the CLI-level check protects `forgeops init` itself
regardless of how or from where it's invoked (a future non-Claude-Code
caller, a different agent harness, a CI job). Both independently
hardcode the same literal path rather than reading it from project
config, so neither a settings edit nor a `pyproject.toml` edit can
silently disable either layer - defense in depth was chosen deliberately
over DRY here. Verified in tests via a monkeypatched
`READONLY_REFERENCE_REPO` pointed at a fake path under `tmp_path` - the
real TrendForge checkout is never used as a test target, per standing
instruction.

## 2026-07-23 — `forgeops worktree create` NAME validation rejects rather than sanitizes, and its registry fails the whole document closed on any bad record

Two deliberate deviations from the nearest existing precedent, both
made for the same reason: a worktree NAME/registry entry controls a
filesystem path and a branch name, so a wrong guess here is worse than
a wrong guess in most other ForgeOps state.

1. `forgeops/worktrees/naming.py:validate_worktree_name` uses a strict
   allow-list (`^[A-Za-z0-9][A-Za-z0-9_-]*$`, plus length/absolute-path/
   reserved-device-name checks) and rejects anything outside it outright
   - it never strips or rewrites a NAME to make it valid. This is what
   makes traversal sequences, separators, spaces, and absolute/drive-
   letter paths impossible by construction, without special-casing each
   one individually, and matches the explicit instruction this
   checkpoint was built under ("reject ambiguous names rather than
   silently changing meaning").
2. `forgeops/state/worktree_registry.py:load_registry` treats *any*
   individual unreadable record as making the *whole* registry
   malformed, unlike `forgeops/state/runtime_registry.py` (the process
   registry this one mirrors in every other way), which skips a bad
   record and keeps the rest. The process registry's consumer
   (`cleanup`) only ever *reads* it to decide what's eligible for
   termination - a dropped bad record just means one fewer termination
   candidate, a safe direction to fail in. The worktree registry's
   consumer (`worktree create`'s preflight) uses an *absence* of a
   matching record as part of its "no conflict" answer - silently
   dropping one unreadable record could make a real conflict invisible.
   Failing the whole document closed (and refusing to create until a
   human resolves it) was chosen over that risk. `worktree list`
   remains unaffected: it already treats a malformed registry as a
   warning and keeps listing from Git's own (independently authoritative)
   state regardless.

See `docs/worktrees.md` for the full NAME-validation rule and registry
schema this codifies.

## 2026-07-23 — `forgeops worktree remove` marks registry records `removed` instead of deleting them, and gates on `--confirm` rather than an interactive prompt

Two deliberate choices for this checkpoint:

1. A successful removal sets a record's `status` to a new
   `STATUS_REMOVED` value (`forgeops/state/worktree_registry.py`)
   rather than deleting the record from `WORKTREE_REGISTRY.json`, and
   rather than adding a new field (e.g. `removed_at`). Every existing
   active-record lookup (`worktree create`'s duplicate check, `worktree
   list`'s registration marker, `worktree remove`'s own eligibility
   check) already filters on `status == STATUS_ACTIVE`, so a removed
   record is automatically inert everywhere without touching that
   logic, while still preserving a concise removal/lifecycle history
   instead of losing the record's identity entirely - satisfying the
   checkpoint's explicit instruction to prefer this over unnecessary
   schema expansion.
2. Removal is gated on an explicit `--confirm` flag rather than an
   interactive y/n prompt. The checkpoint's own instructions required
   the command to "remain deterministic and automation-safe" and
   explicitly forbade interactive confirmation - `--confirm` (mirroring
   `cleanup --execute`'s established dry-run-by-default shape in this
   same package) keeps `forgeops worktree remove` scriptable and
   testable without stdin interaction, while still requiring an
   unambiguous, explicit opt-in distinct from `--dry-run` before any
   mutation happens. Missing `--confirm` on a non-dry-run invocation
   returns `BLOCKED` (2) with `data.action == "confirmation_required"`
   after running the identical full preflight `--dry-run` would - so a
   caller always sees exactly what would happen before opting in.

Branch deletion (`--delete-branch`) reuses the same `--confirm` gate
rather than a separate flag, and is restricted to the `forgeops/<name>`
namespace with a TOCTOU tip-commit recheck immediately before the
actual (always non-force) `git branch -d` call - see `docs/worktrees.md`
"Branch deletion (opt-in)" for the full eligibility list.

## 2026-07-23 — Task Specification Engine: `BLOCKED` reused for "task not found", `task validate`'s blockers stay non-fatal for `task show`, and a single shared repo-level gate replaces per-command duplication

Three deliberate choices made building `forgeops task create|show|list|validate|close`:

1. `task show`/`task validate` both return `BLOCKED` (2) when the
   requested task ID cannot be located at all (invalid ID format, or no
   directory and no index entry) - not a new exit code. `exit_codes.py`
   already documents `BLOCKED` generically as "a blocking safety
   finding was reported", and `forgeops audit` (itself read-only)
   already established that `BLOCKED` isn't reserved for mutating
   commands. Introducing a new code for "couldn't find the named
   resource" would violate this repo's own stability guarantee
   (`docs/cli-exit-codes.md`: "new distinction... expressed as a new
   checks[]/data field, not a new exit code").
2. `forgeops/state/task_validate.py:validate_task` classifies every
   issue as a `blocker` or a `warning` - but only `task validate` (and
   `task close`'s own preflight, which reuses the same function) treats
   a `blocker` as something that actually refuses anything. `task show`
   surfaces every issue (blocker or warning) as a non-fatal warning
   check and only ever hard-refuses on "not found" - `show` is for
   *seeing* problems (including a fresh `draft` task's expected-empty
   Acceptance Criteria), `validate`/`close` are where the same finding
   actually blocks something. Reusing one classification function for
   both, with the caller deciding what to do with `blocker` severity,
   avoided a second, parallel issue-classification implementation.
3. All five task commands share one gate,
   `forgeops/cli/task.py:_repo_level_block` (protected reference
   repository, then "is this an initialized ForgeOps project" via
   `forgeops.state.schema.check_current_state`) - checked once, before
   any command-specific logic runs, rather than five separate
   near-duplicate checks. `forgeops task create`'s own plan builder
   (`forgeops/state/task_create.py:build_task_create_plan`) *also*
   contains its own copy of both checks internally, purely for direct
   unit-testability and to mirror `worktree_create.py`'s established
   precedent of defense-in-depth duplication - in the real CLI flow
   `_repo_level_block` always intercepts first, so that internal copy
   is normally dead code, exercised only by tests that call
   `build_task_create_plan` directly.

See `docs/tasks.md` for the full command/exit-code/lifecycle contract
this codifies.
