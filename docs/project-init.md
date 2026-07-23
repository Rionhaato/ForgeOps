# `forgeops init` — Safe Project Initialization

## What it does

`forgeops init [PATH]` bootstraps the minimum ForgeOps governance
structure for a target project: a root `CLAUDE.md` and four files under
`.agent/` (`CURRENT_STATE.json`, `PROJECT_FACTS.md`, `DECISIONS.md`,
`HANDOFF.md`). It is the only mutating command in this checkpoint whose
target directory is chosen directly by the caller rather than discovered
by walking up for `.git` — see "Target resolution" below.

## Target resolution

Unlike every other ForgeOps command, `init` never calls
`forgeops.core.paths.resolve_repo_root` (which walks upward looking for
a `.git` entry). The target is exactly:

- the `PATH` argument, if given, or
- the current working directory, if omitted.

This is deliberate: `init` must never silently initialize a different
parent or child repository than the one explicitly requested. A nested
directory one level below an already-governed parent gets its own,
independent `.agent/` structure — the parent's files are never touched
(see `test_does_not_traverse_into_parent_or_child_repository` in
`tests/integration/test_cli_init.py`).

Rejected targets (`REPO_NOT_FOUND`, exit 4 — reused rather than
introducing a new exit code, per the stability guarantee in
`docs/cli-exit-codes.md`):
- path does not exist,
- path exists but is not a directory.

## TrendForge / read-only reference repository protection

`forgeops init` refuses to initialize the configured read-only reference
repository (`C:\Users\joshd\TrendForge`, `forgeops.core.paths.READONLY_REFERENCE_REPO`)
or any path beneath it — checked with a pure, filesystem-free string
comparison (`is_protected_reference_path`) before any existence check,
so the rejection is instant and unconditional. Exit code `BLOCKED` (2).

This is a **second, independent enforcement layer** on top of the
existing `.claude/hooks/pretooluse_safety.py` PreToolUse hook (which
blocks tool calls referencing TrendForge before they ever run). The two
are deliberately not unified — the hook protects any tool call in this
session; this check protects `forgeops init` specifically even if
invoked outside a hook-covered context (e.g. a future non-Claude Code
caller). Both hardcode the same path rather than reading it from
project config, so a config edit can never silently disable either.

## Ownership and conflict model

Every managed path is classified as exactly one of:

| State | Meaning |
|---|---|
| `missing` | Path does not exist yet — will be created. |
| `compatible` | Path exists and is recognizably ForgeOps-owned — left untouched, not rewritten. |
| `conflict` | Path exists and is not recognizably ForgeOps-owned (or is malformed) — blocks the entire run. |

**`.agent` (directory):** `compatible` if it already exists as a
directory; `conflict` if a plain file exists at that path instead.

**`.agent/CURRENT_STATE.json`:** compatibility is decided by the exact
same machinery `forgeops checkpoint`/`forgeops handoff` already use
(`forgeops.state.schema.is_supported_schema_version`,
`forgeops.state.checkpoint.load_previous_state`'s JSON/shape checks) —
valid JSON, a JSON object, with a supported `schema_version`. Note this
is **more conservative than `forgeops checkpoint`**: checkpoint treats
an unsupported/malformed previous state as a `warning` and rewrites it
with fresh narrative defaults; `init` treats the same condition as a
blocking `conflict` and writes nothing. This is intentional — `init` is
a first-time bootstrap and must never risk silently discarding a
project's existing (if unusual) state; `checkpoint`'s job, recomputing a
fresh snapshot on every call, is different enough to justify the
different default.

**`.agent/PROJECT_FACTS.md`, `.agent/DECISIONS.md`, `.agent/HANDOFF.md`,
`CLAUDE.md`:** compatibility is decided by an ownership marker — the
literal first line `<!-- forgeops:managed schema=1 -->`
(`forgeops.state.project_init.FORGEOPS_MANAGED_MARKER`). Present →
`compatible` (including a `CLAUDE.md` a previous `forgeops init` run
created — this is what makes a second run idempotent rather than
reporting a false conflict on its own prior output). Absent (including
no marker at all, e.g. a user's own hand-written `CLAUDE.md`) →
`conflict`. **No merge/append strategy is implemented in this
checkpoint** — a conflicting file is reported, with a recommendation to
review it manually, and is never rewritten or appended to.

If **any** managed path is a `conflict`, the entire run is blocked
(`BLOCKED`, exit 2) and **nothing is written** — not even the paths that
would otherwise have been fine to create. This is checked in preflight,
before any write is attempted.

## Idempotency and partial initialization

Running `init` again on an already-initialized project is a no-op for
every `compatible` path (untouched, not rewritten) and completes any
`missing` path — so a partially-initialized project (e.g. one where a
prior run was interrupted, or someone deleted `.agent/HANDOFF.md` by
hand) is safely completed on the next run rather than blocked or
duplicated. Each managed path's individual state is visible in
`data.paths[]` (JSON) and the `preflight-*` checks (human output), so
"partial" is always observable, not silently papered over.

## Atomic writes and rollback

Every file write goes through the existing
`forgeops.state.atomic_write.atomic_write_text` (same-filesystem
temp-file + `os.replace`, used by `checkpoint`/`handoff`). Directories
use plain `mkdir`. If any single write in a run fails (`OSError`), every
path *this run* created is removed again, in reverse creation order,
before the command returns — so a failed `init` never leaves a
half-initialized project. Paths that were already present and
`compatible` before this run are never touched by rollback, since they
were never in the "created this run" set to begin with. See
`test_apply_init_plan_rolls_back_new_paths_on_write_failure` and
`test_apply_init_plan_never_rolls_back_preexisting_compatible_paths` in
`tests/unit/test_project_init.py`.

## Dry-run

`--dry-run` performs the identical preflight (including conflict
detection) and reports what would happen — `would_create` /
`would_preserve` per path — without writing anything. **Dry-run returns
the same exit code a real run would**, including `BLOCKED` when a
conflict is detected, so a caller scripting against the exit code gets
an honest preview rather than an always-`SUCCESS` dry-run that then
fails for real.

## Non-Git behavior

`forgeops init` works in a directory with no `.git` at all — git is
optional for this command alone. `is_git_repo` is a plain
`(target / ".git").exists()` check, never a git subprocess call; the git
executable itself is only invoked (via `forgeops.core.git.get_branch_state`)
when a `.git` entry is actually present, and even then a missing git
executable degrades to `branch: null` rather than failing (the same
graceful-degradation behavior `forgeops checkpoint` already relies on
for a repository with zero commits). `init` never runs `git init` itself.

## Content generated

- **`.agent/CURRENT_STATE.json`** reuses
  `forgeops.state.checkpoint.build_checkpoint_data` — the exact same
  pure builder `forgeops checkpoint`/`forgeops handoff` use — so a
  freshly initialized project's state file is immediately compatible
  with a later `forgeops checkpoint` run (same `schema_version`, same
  required keys). Only the initial narrative (`mission`,
  `completed_work`, `next_action`, ...) differs from a from-scratch
  `checkpoint` run.
- **`.agent/PROJECT_FACTS.md`, `.agent/DECISIONS.md`,
  `.agent/HANDOFF.md`, `CLAUDE.md`** are small, hardcoded markdown
  templates (`forgeops/state/project_init.py`) — not read from
  `shared/templates/*` (that installer-template system is Phase 12+
  scope, out of bounds for this checkpoint).

The generated `CLAUDE.md` is deliberately small: it identifies ForgeOps
governance, points at `.agent/PROJECT_FACTS.md`/`DECISIONS.md`/`HANDOFF.md`/
`CURRENT_STATE.json` rather than duplicating them, requires preserving
working code and validation evidence, forbids credentials in tracked
files/prompts/logs/state, requires explicit approval for destructive
actions/commits/pushes/installs/credentials/spending/deployment/external
communication, states that ForgeOps inspection commands are read-only,
and states that mutations stay within the initialized project.

## Explicit non-goals (this checkpoint)

- **No `.agent/runtime/`, `.agent/checkpoints/`, `.agent/logs/`
  directories are pre-created.** Nothing in this checkpoint requires
  them ahead of time: `forgeops.state.runtime_registry` (the only
  current writer that would use `.agent/runtime/`) already creates its
  own parent directory via `atomic_write_text` the first time it writes
  a registry record. Pre-creating empty, unused directories was
  explicitly out of scope.
- No templates read from `shared/templates/*` (Phase 12+ installer
  scope).
- No merge/append strategy for a conflicting existing file — a conflict
  is reported, never resolved automatically.
- No agents, worktrees, MCP profiles, approval orchestration,
  installers, Rocky integration, deployment workflows, or dependency
  installation — this command only writes the files listed above.
- Does not run `git init`, configure a remote, commit, or push.

## Exit codes

See `docs/cli-exit-codes.md`'s `init` entry for the full precedence
table. Summary: `BLOCKED` (2) for a conflict or a TrendForge target;
`REPO_NOT_FOUND` (4) for a missing/non-directory target; `COMMAND_EXECUTION_FAILURE`
(5) for a real write failure (rolled back); `SUCCESS` (0) otherwise —
`init` never returns `WARNINGS_PRESENT`; a fresh or partially-complete
initialization proceeding normally is not a warning-worthy condition.
