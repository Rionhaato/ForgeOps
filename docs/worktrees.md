# Git Worktrees (`forgeops worktree list` / `forgeops worktree create`)

A safe foundation for isolated parallel work: read-only inspection of a
repository's Git worktrees, and bounded, conflict-checked creation of a
new one. This checkpoint deliberately implements only these two
commands - see "Explicit non-goals" below.

## Commands

```
forgeops worktree list [--repo PATH]
forgeops worktree list [--repo PATH] --json

forgeops worktree create NAME [--repo PATH]
forgeops worktree create NAME [--repo PATH] --branch BRANCH
forgeops worktree create NAME [--repo PATH] --base REF
forgeops worktree create NAME [--repo PATH] --dry-run
forgeops worktree create NAME [--repo PATH] --json
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
      "task_id": null, "agent_id": null
    }
  ]
}
```

`task_id`/`agent_id` exist in the schema for a future checkpoint to
populate; this checkpoint always writes them as `null`. Unlike the
process registry (which skips an individual malformed record), **any**
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

## Explicit non-goals (this checkpoint)

No `worktree remove`, no `worktree prune`, no branch deletion, no merge
orchestration, no write-capable agents, no parallel task routing, no
approvals, no MCP, no automatic commits. A partial failure's leftover
branch/directory/registration is never cleaned up automatically.
