# `forgeops checkpoint` and `forgeops handoff`

Two deterministic state writers that let a Claude Code or Codex session
resume safely across a context-window reset or a brand-new session,
without re-investigating the repository from scratch.

## What each command writes

| Command | Writes | Format |
|---|---|---|
| `forgeops checkpoint` | `.agent/CURRENT_STATE.json` (the existing canonical location) | machine-readable JSON |
| `forgeops handoff` | `.agent/HANDOFF.md` (the existing canonical location) | human/agent-readable markdown |

Both are read-only except for that one documented file each. Neither
installs anything, invokes model reasoning, or modifies application
source. Neither ever touches a configured read-only reference repository
(see "Read-only reference repository" below) - they don't even inspect
it; they only *quote back* whatever path a project's own `CURRENT_STATE.json`
already recorded under `repository.reference_repo_readonly`.

## Determinism: what's recomputed vs. what's preserved

`forgeops/state/checkpoint.py:build_checkpoint_data()` is the single pure
function behind both commands. Every call recomputes these fields fresh
from git and the filesystem - never from the previous document:

- `branch`, `head`
- `remote.configured` / `.names` / `.upstream` / `.ahead` / `.behind`
- `changed_files.staged` / `.modified` / `.untracked`
- `has_local_changes`
- `detected_stack` (technology/confidence/ambiguous/evidence_count only -
  deliberately no raw evidence paths, to keep the document small and
  avoid recording unnecessary absolute paths)

These fields are **carried forward unchanged** from the previous
`CURRENT_STATE.json` (if one exists and its `schema_version` is
supported), because no deterministic script can honestly synthesize them
from git state alone - they're the narrative a human or a prior session
wrote:

- `repository`, `mission`, `completed_work`, `recent_tests`, `blockers`,
  `active_agents`, `owned_files`, `active_worktrees`, `pending_approvals`,
  `background_processes`, `last_checkpoint`, `next_action`

If no previous document exists, these all start as empty defaults
(`""`, `[]`, or `{}`) rather than being invented.

## `forgeops handoff` is derived from checkpoint data, not a second source

`run_handoff()` calls the exact same `build_checkpoint_data()` function
`run_checkpoint()` does (reading whatever `CURRENT_STATE.json` currently
exists, or computing a fresh snapshot if none does) and renders it as
markdown, plus a small set of static constants mirrored from this
repo's own `CLAUDE.md`:

- **Standard validation commands** (`forgeops/cli/handoff.py:STANDARD_VALIDATION_COMMANDS`)
  mirrors `CLAUDE.md` section 8 - update both together.
- **Approval boundaries** (`APPROVAL_BOUNDARY_CATEGORIES`) mirrors
  `CLAUDE.md` section 11 and this repo's own established
  `.agent/HANDOFF.md` wording.
- **Prohibited actions** (`PROHIBITED_ACTIONS`) mirrors `CLAUDE.md`
  section 7's destructive-operation rules.

`forgeops handoff` never re-derives checkpoint data independently - if
`build_checkpoint_data()`'s logic ever changes, both commands' output
changes together automatically.

## Consistency model: why there is no dual-file transaction

Each command writes **exactly one file**, atomically. There is no code
path where a single invocation of either command writes both
`CURRENT_STATE.json` and `HANDOFF.md` - so there is no scenario requiring
a two-file transaction, and no rollback machinery was built for one.
Running `forgeops checkpoint && forgeops handoff` is two independent
atomic operations; if the first fails, the second still runs against
whatever `CURRENT_STATE.json` already existed (or a fresh computed
snapshot if none did) - it never blocks on the first command's success.
If you want both refreshed together, run both commands; each is
independently safe to retry.

## Atomic-write guarantee

Both commands use `forgeops/state/atomic_write.py:atomic_write_text()`:

1. Write the new content to a temporary file in the *same directory* as
   the destination (so the final step is a same-filesystem rename).
2. Flush and `os.fsync()` the temporary file.
3. `os.replace()` the destination with the temporary file - one atomic
   filesystem operation on both POSIX and Windows.
4. On any failure, the temporary file is removed and the destination is
   left completely untouched - there is never a partially-written
   `CURRENT_STATE.json` or `HANDOFF.md` visible to another process.

## Schema version and evolution

`CURRENT_STATE.json`'s `schema_version` is currently `1`. Supported
versions live in exactly one place, `forgeops/state/schema.py:SUPPORTED_SCHEMA_VERSIONS`
(imported by the checkpoint writer, not duplicated). If an existing
`CURRENT_STATE.json` has an unsupported (e.g. future) `schema_version`,
both commands:

- report it as a `warning`-status check (`previous-state-schema`), never
  a hard failure,
- reset narrative fields to empty defaults rather than guessing at an
  unknown document shape,
- still write a fresh, valid, `schema_version: 1` document.

When the schema needs to evolve, increment
`forgeops/state/checkpoint.py:CURRENT_STATE_SCHEMA_VERSION` and add the
new version to `SUPPORTED_SCHEMA_VERSIONS` deliberately, preserving
backward-compatible reads where practical (this is exactly the
mechanism that already protects against an unsupported *future* version
today).

## Dry-run

`--dry-run` on either command computes the full document/markdown and
reports it in `data.checkpoint` / `data.handoff_markdown` (`--json`) or
an `informational` check (human mode) - no file is written. Safe to run
repeatedly to preview what a real invocation would produce.

## Exit codes

Both commands use the same seven shared codes (`docs/cli-exit-codes.md`):

- `SUCCESS` (0) - written cleanly (or, in `--dry-run`, would have been).
- `WARNINGS_PRESENT` (1) - e.g. the previous state document had an
  unsupported `schema_version` (recovered, not blocked).
- `REPO_NOT_FOUND` (4) - no git repository discovered.
- `COMMAND_EXECUTION_FAILURE` (5) - `git` itself unavailable, or the
  atomic write failed (disk full, permission denied, ...) - reported as
  a `fail`-status check (`checkpoint-write` / `handoff-write`), mirroring
  how `doctor`'s `log-dir-writable` check handles a write failure.
- `INTERNAL_ERROR` (6) - an unexpected bug, caught by the existing
  top-level exception boundary in `forgeops/cli/__init__.py:main()` -
  neither command adds its own catch-all.

## Secret safety

Neither command reads environment variables, full command output, or
anything from outside `repo_root` plus the previous state document
itself. `detected_stack` records only technology/confidence/counts, not
raw file paths. Nothing is redacted post-hoc because nothing
secret-shaped is ever collected in the first place - verified by
dedicated tests that plant a secret-shaped environment variable and
assert it never appears in the written document, the JSON output, or
the human-rendered text.

## Files these commands are permitted to modify

- `forgeops checkpoint`: `.agent/CURRENT_STATE.json` only (plus
  `logs/checkpoint/<timestamp>/checkpoint.log`, the same logging
  side-channel every other command already uses).
- `forgeops handoff`: `.agent/HANDOFF.md` only (plus
  `logs/handoff/<timestamp>/handoff.log`).

Enforced by dedicated regression tests
(`tests/integration/test_checkpoint_handoff_scope.py`) that snapshot the
entire working tree before and after each command and assert the only
changed path is the one documented above.

## How a future agent should resume from a handoff

1. Read `.agent/HANDOFF.md` (this file) in full.
2. Read `.agent/DECISIONS.md` for any entries relevant to the current task.
3. Run the "Commands the next agent should run first" list in the
   handoff, in order.
4. Resume the exact next action recorded in "Exact recommended next
   checkpoint" rather than re-investigating already-completed work.
5. Respect "Actions that remain prohibited without explicit approval" -
   these are not suggestions.
6. If a read-only reference repository is named, never modify it, never
   commit to it, never run a mutating command against it.
7. Run `forgeops checkpoint` again once new work is complete, so the
   next session (or the next handoff) reflects it.
