# Git Worktrees (`forgeops worktree list` / `create` / `remove`)

A safe foundation for isolated parallel work: read-only inspection of a
repository's Git worktrees, bounded conflict-checked creation of a new
one, and confirmation-gated, eligibility-checked removal of a
ForgeOps-created one (and, optionally, its ForgeOps-owned branch). See
"Explicit non-goals" below for what is still deliberately out of scope.

## Commands

```
forgeops worktree list [--repo PATH]
forgeops worktree list [--repo PATH] --json

forgeops worktree create NAME [--repo PATH]
forgeops worktree create NAME [--repo PATH] --branch BRANCH
forgeops worktree create NAME [--repo PATH] --base REF
forgeops worktree create NAME [--repo PATH] --dry-run
forgeops worktree create NAME [--repo PATH] --json

forgeops worktree remove NAME [--repo PATH]
forgeops worktree remove NAME [--repo PATH] --dry-run
forgeops worktree remove NAME [--repo PATH] --confirm
forgeops worktree remove NAME [--repo PATH] --delete-branch --confirm
forgeops worktree remove NAME [--repo PATH] --json
```

`forgeops worktree` with no subcommand is a plain argparse usage error
(exit 2) - a subcommand is required, matching how every other
two-level ForgeOps subcommand behaves.

## Managed worktree root

```
<repository-parent>/.forgeops-worktrees/<repository-name>/<name>
```

A sibling of the repository, never inside it, so the source checkout's
own `git status` never sees worktree scaffolding. Computed deterministically
in `forgeops/worktrees/naming.py:managed_root_for`/`worktree_path_for`.

## NAME validation and sanitization

A NAME must match `^[A-Za-z0-9][A-Za-z0-9_-]*$`, be at most 100
characters, not be an absolute path, and not be a Windows reserved
device name (`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`,
case-insensitive). Anything else is **rejected outright**, never
silently stripped or rewritten - `forgeops/worktrees/naming.py:validate_worktree_name`.
This single allow-list rule is what makes traversal sequences (`..`),
separators (`/`, `\`), spaces, and absolute/drive-letter paths all
impossible by construction, without special-casing each one.

## Branch naming

If `--branch` is omitted, the branch is derived deterministically as
`forgeops/<name>`. `forgeops worktree create` always creates a **new**
branch (`git worktree add -b`) - it never checks out or reuses an
existing branch, and never resets one. If the resolved branch name
(derived or explicit) already exists, or is already checked out in
another worktree, creation is refused with a `branch-already-exists` or
`branch-checked-out-elsewhere` conflict respectively.

## Base ref resolution

If `--base` is omitted, the base is `HEAD`, resolved to a commit SHA
during preflight (`git rev-parse --verify <ref>^{commit}`) and passed
to `git worktree add` as that fixed SHA - never the moving ref name -
so a concurrent branch update between preflight and creation can never
change what the new worktree is based on. An unresolvable base ref is a
`base-ref-not-found` conflict.

## Preflight / conflict model

`forgeops/state/worktree_create.py:build_worktree_create_plan` is a
read-only function - it never writes anything - shared by both
`--dry-run` and a real run, so both always agree on whether creation
would proceed. It checks, in this order, and collects every conflict
found (not just the first):

- protected reference repository (TrendForge) - `protected-reference-repo`
- bare repository - `bare-repository` (see "Known limitation" below)
- invalid NAME - `invalid-name`
- destination path already exists on disk - `destination-exists`
- destination path outside the managed root - `outside-managed-root`
  (defensive; unreachable via the CLI given NAME validation, but tested
  directly against the plan builder)
- `git worktree list` itself failing - `git-worktree-list-failed`
- destination already a registered Git worktree - `duplicate-worktree`
- requested branch already checked out elsewhere - `branch-checked-out-elsewhere`
- requested branch already exists - `branch-already-exists`
- base ref does not resolve to a commit - `base-ref-not-found`
- the worktree registry is malformed - `registry-malformed` (fails
  closed rather than risk missing a real conflict it can't see)
- destination already has an active registry entry - `duplicate-registry-entry`

Any conflict blocks the entire run - `--dry-run` and a real run both
return `BLOCKED` (exit 2) with the identical conflict list in `data.conflicts`.

## Dry-run

`--dry-run` runs the identical preflight and reports the resolved
branch, base ref/commit, proposed worktree path, every conflict found,
and `data.would_proceed` - performing zero Git or filesystem mutation.
Human and JSON output agree on every field.

## Atomicity and partial-failure reporting

`git worktree add` is not a single atomic filesystem operation. If it
fails, `forgeops/state/worktree_create.py:apply_worktree_create` detects
and reports whatever partial state remains (`directory_created`,
`branch_created`, `worktree_registered_in_git`) plus a manual recovery
recommendation - it never force-removes anything, never runs `git
worktree prune`, and never runs `git branch -D`. Cleanup of a partial
failure is left to a human, by design, in this checkpoint.

## Registry

`.agent/runtime/WORKTREE_REGISTRY.json` (`forgeops/state/worktree_registry.py`),
schema-versioned and atomically written, mirroring
`forgeops/state/runtime_registry.py`'s (the process registry) shape and
safety properties. One record per created worktree:

```json
{
  "schema_version": 1,
  "records": [
    {
      "id": "…", "name": "demo", "path": "…", "branch": "forgeops/demo",
      "base_commit": "…", "created_at": "…", "status": "active",
      "task_id": null, "agent_id": null, "updated_at": "…"
    }
  ]
}
```

`task_id` is populated by `forgeops task assign`/`forgeops task
unassign` (see docs/tasks.md "Ownership") - the Task Ownership
checkpoint this field was originally reserved for. `agent_id` remains
reserved for a future checkpoint, always `null` for now. `updated_at`
(added by the same checkpoint) is set at creation and whenever
`task_id`/`agent_id` change; `null` on any record predating this field,
which is always backward-compatible to read. Unlike the process
registry (which skips an individual malformed record), **any**
unreadable record marks the *whole* registry malformed - worktree
conflict-detection needs "no matching record" to reliably mean "no
matching record", not "one existed and was silently dropped". A
malformed registry blocks `worktree create` outright (fails safe); it
never blocks `worktree list`, which still works from Git's own state
and reports the malformed registry as a warning.

Stale entries (registered but no longer a real Git worktree - e.g. removed
directly via `git worktree remove`) are detected and reported by
`worktree list` as a warning; they are never automatically removed -
that is explicit future scope, not this checkpoint's.

**`worktree remove` does not clear task ownership.** Removing a
worktree that is currently assigned to a task leaves that task's
`TASK.json.worktree_id` pointing at a now-removed worktree - this is by
design (see docs/tasks.md "Removing an assigned worktree"): `forgeops
worktree remove`'s own scope is deliberately unchanged by the Task
Ownership checkpoint, and the resulting orphaned ownership is exactly
what `forgeops task validate`'s ownership-consistency checks exist to
detect (never auto-repaired). Run `forgeops task unassign` first to
keep ownership consistent proactively.

## Exit codes

- `REPO_NOT_FOUND` (4) - no repository discoverable at/above the given
  path. A true bare repository (no `.git` entry anywhere, since the
  bare directory itself plays that role) cannot be discovered by the
  shared `forgeops.core.paths.resolve_repo_root` every command reuses,
  so it is rejected via this code, not `BLOCKED` - see "Known
  limitation" below.
- `COMMAND_EXECUTION_FAILURE` (5) - git unavailable, `git worktree list`
  itself failing to run, or `git worktree add` failing (partial-state
  detected and reported, nothing rolled back automatically).
- `BLOCKED` (2) - the read-only reference repository, or any preflight
  conflict listed above.
- `WARNINGS_PRESENT` (1) - `worktree list`: a registry-schema warning, a
  stale registry entry, or a malformed-porcelain-output warning.
  `worktree create`: the worktree itself was created successfully but
  the registry could not be updated afterward.
- `SUCCESS` (0) - otherwise, including a conflict-free dry-run.

## Known limitation

`forgeops.core.paths.resolve_repo_root` (shared by every command) finds
a repository by walking upward for a `.git` entry. A true bare
repository has no such entry - the directory itself serves that role -
so it can never be discovered as a repository root at all, regardless
of `--repo` pointing directly at it. `forgeops worktree create` still
refuses to act on one (`REPO_NOT_FOUND`, not a false `SUCCESS`), just
via repo-discovery rather than the `bare-repository` preflight
conflict; that conflict path is real and exercised directly at the
`build_worktree_create_plan` level (`tests/unit/test_worktree_create.py`)
for the hypothetical case of a bare repository nested in a way
discovery could reach. Changing the shared discovery function to
recognize bare repositories was out of scope for this checkpoint -
every other command reuses it unmodified, and altering it has
blast radius beyond worktrees.

## Worktree removal (`forgeops worktree remove`)

Confirmation-gated by design - removal mutates the filesystem, Git's
worktree registration, and the ForgeOps registry, so it never runs
without an explicit signal:

- with neither `--dry-run` nor `--confirm`: runs the full preflight and
  reports what would be removed, but makes no mutation and returns
  `BLOCKED` (2) with `data.action == "confirmation_required"`;
- `--dry-run`: identical preflight, never requires `--confirm`, never
  mutates, and returns the same exit code a confirmed run would (`SUCCESS`
  if the plan is clean, `BLOCKED` if the plan has a conflict);
- `--confirm`: performs the real, single mutating removal.

No interactive confirmation prompt exists anywhere - the command stays
deterministic and automation-safe.

### Eligibility (preflight)

`forgeops/state/worktree_remove.py:build_worktree_remove_plan` is a
read-only function - shared by `--dry-run`, the missing-`--confirm`
response, and a real run - that collects every conflict found (not just
the first). A worktree is only ever removed when **all** of the
following hold; any violation adds a conflict and blocks the whole
operation:

- NAME is valid (`invalid-name` otherwise - rejects absolute paths and
  traversal sequences the same way `worktree create` does);
- an **active** record for NAME exists in
  `.agent/runtime/WORKTREE_REGISTRY.json` (`not-registered` otherwise -
  this is also what refuses a Git-only worktree that was never
  registered by ForgeOps);
- the registry is schema-valid (`registry-malformed` otherwise, fails
  closed);
- exactly one active record matches the name/path (`duplicate-registry-entry`
  otherwise - an ambiguous registry is never guessed at);
- the record's path matches the deterministic path for NAME and resolves
  beneath the managed root (`registry-path-mismatch` / `outside-managed-root`
  otherwise);
- it is not (or beneath) the read-only reference repository
  (`protected-reference-repo`);
- it is not owned by a task or agent (`active-task-ownership` /
  `active-agent-ownership` - always false today since no checkpoint yet
  populates these fields, but checked defensively for when one does);
- Git still lists it as a worktree (`stale-registry-entry` otherwise -
  the registry is never trusted alone);
- it is not the primary checkout (`primary-checkout`);
- Git's current branch for the worktree matches the registry's recorded
  branch (`identity-mismatch` - this is what refuses a worktree someone
  manually detached or re-checked-out since it was created);
- it is not locked (`worktree-locked`);
- it has no uncommitted changes - staged, modified, or untracked
  (`dirty-worktree`, via `forgeops.core.git.get_status`);
- no Git operation is in progress - merge, rebase, cherry-pick, revert,
  bisect (`git-operation-in-progress`, via `forgeops.core.git.get_operation_state`);
- no live process is registered (in `.agent/runtime/PROCESS_REGISTRY.json`)
  against this exact worktree path (`active-managed-process` - only
  checked, and only pays the OS process-enumeration cost, when a
  registry record's `repository_root` already matches the worktree path;
  `working_directory` is always `None` on Windows - see
  `forgeops/detectors/processes.py` - so an exact registry match is the
  only evidence this checkpoint trusts).

Never terminates a process, stashes, commits, resets, or cleans files -
a dirty or busy worktree is reported with a manual-recovery message and
left completely untouched.

### Removal mechanics

For a clean, eligible, confirmed worktree,
`forgeops/state/worktree_remove.py:apply_worktree_remove`:

1. Revalidates identity (still Git-listed, branch still matches)
   immediately before mutating - closing the gap between preflight and
   this call (compare-before-write, reduces TOCTOU risk).
2. Runs `git worktree remove <path>` - **never** `--force`. Git itself
   independently refuses a dirty or locked worktree, a second safety
   layer beyond this package's own preflight.
3. Verifies Git no longer lists the path and the directory no longer
   exists. Only if *both* hold is the removal `ok`; if either doesn't
   (a form of partial failure - see below), nothing further is done.
4. Only then marks the record `removed` (`STATUS_REMOVED` in
   `forgeops/state/worktree_registry.py`) via an atomic write -
   the record is preserved as concise removal/lifecycle history rather
   than deleted or given a new schema field. A failed `git worktree
   remove` never touches the registry at all.
5. If `--delete-branch` was requested and the branch remained eligible
   (see below), attempts the branch deletion.

Never uses `git worktree remove --force`, `git worktree prune`, or a
recursive filesystem delete as the removal mechanism.

### Partial-failure reporting

Every partial state is reported explicitly, never silently upgraded to
success and never automatically force-cleaned:

- `git worktree remove` itself fails: nothing changes, `COMMAND_EXECUTION_FAILURE`
  (5), registry untouched.
- `git worktree remove` reports success but Git still lists the path, or
  the directory still exists on disk: reported as a partial failure
  (`COMMAND_EXECUTION_FAILURE`), registry untouched (only a fully
  verified removal is ever reflected in the registry).
- Identity changed between preflight and apply (e.g. someone detached
  HEAD in the worktree in between): refused before any mutation is
  attempted, `COMMAND_EXECUTION_FAILURE`.
- Worktree removed but the registry write itself fails (disk full,
  permission denied): `WARNINGS_PRESENT` (1) - the destructive action
  genuinely completed; only the bookkeeping lagged, mirroring how
  `worktree create` handles the same situation.
- Worktree removed but branch deletion was requested and refused (not
  fully merged, or became ineligible - see below): `WARNINGS_PRESENT`,
  `data.action` still `removed_worktree`, branch left intact.

Every partial-failure response includes `data.partial_state` and a
`data.manual_recovery_recommendation` - never a full success claim.

### Branch deletion (opt-in)

By default the branch is **preserved** - only the worktree and its
active registry entry are removed. Deletion happens only with
`--delete-branch --confirm` together, and only when every condition
holds (checked by `BranchDeletePrecheck` at preflight, revalidated
immediately before the actual `git branch -d` call):

- the branch is in the ForgeOps-owned namespace, `forgeops/<name>` (a
  worktree created with an explicit `--branch` outside that namespace
  is never deleted);
- it matches the registry record exactly;
- it exists locally and is not checked out in any other worktree;
- its tip commit still matches the tip captured at preflight time (a
  TOCTOU guard - if the branch moved in between, deletion is skipped,
  never attempted against a moved target).

Deletion itself is always `git branch -d` (`forgeops/worktrees/git_worktree.py:delete_branch_safe`)
- **never** `-D`, and never a remote deletion. If the branch is not
  fully merged, Git's own refusal is surfaced as-is: the branch is left
  intact, the worktree removal (already completed) is still reported,
  and the overall result is `WARNINGS_PRESENT`. Ineligibility (wrong
  namespace, checked out elsewhere, moved tip) is reported the same way
  - never escalated, never forced.

### Exit codes (`worktree remove`)

- `REPO_NOT_FOUND` (4) / `COMMAND_EXECUTION_FAILURE` (5, git unavailable)
  as usual.
- `BLOCKED` (2) - the read-only reference repository, any preflight
  conflict listed above, or `--confirm` missing on a non-dry-run
  invocation (`data.action == "confirmation_required"`). `--dry-run`
  returns the same code a confirmed run would when the plan itself is
  blocked.
- `COMMAND_EXECUTION_FAILURE` (5) - `git worktree remove` itself failed,
  or reported success without the removal being fully verifiable (see
  "Partial-failure reporting").
- `WARNINGS_PRESENT` (1) - the worktree was removed but the registry
  write failed, or branch deletion was requested but refused/ineligible.
- `SUCCESS` (0) - the worktree was removed (and, if requested, the
  branch was too) with no partial state, or a conflict-free `--dry-run`.

## Explicit non-goals

No `worktree prune`, no bulk/sweep removal, no forced removal, no merge
orchestration, no write-capable agents, no parallel task routing, no
approvals, no MCP, no automatic commits, no deployment. A partial
failure's leftover branch/directory/registration is never cleaned up
automatically.
