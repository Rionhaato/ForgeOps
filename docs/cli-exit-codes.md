# CLI Exit Codes

Defined once in `forgeops/core/exit_codes.py` and used identically by
every command — no command defines its own ad-hoc codes.

| Code | Name | Meaning |
|---|---|---|
| 0 | `SUCCESS` | No warnings, no blocking findings. |
| 1 | `WARNINGS_PRESENT` | Completed with one or more non-blocking warnings (e.g. an optional tool missing, a state file present but invalid). |
| 2 | `BLOCKED` | A blocking safety finding was reported — `forgeops audit` directly (a likely secret, or a dangerous filename pattern in working-tree changes), or `forgeops release-check` when its aggregated audit result is blocked. |
| 3 | `INVALID_CONFIG` | `[tool.forgeops]` in `pyproject.toml` exists but is malformed (bad TOML, wrong value type, non-table section). |
| 4 | `REPO_NOT_FOUND` | No git repository could be discovered at or above the given path (or an explicit `--repo` path doesn't exist). |
| 5 | `COMMAND_EXECUTION_FAILURE` | A required external command failed or was unavailable — `git` itself, or (since Phase 2B) a selected `forgeops test`/`release-check` command that didn't complete successfully (nonzero exit, timeout, or missing executable). |
| 6 | `INTERNAL_ERROR` | An unexpected internal ForgeOps error. Raised by the top-level exception boundary in `forgeops/cli/__init__.py:main()` since Phase 2B (Part 5) — see "Top-level exception handling" in `docs/cli-architecture.md`. |

## Precedence

Each command computes its own worst-status exit code from its `checks[]`
list using a fixed precedence order (worst first), implemented per-command
as `_compute_exit_code()` in `doctor.py`/`status.py`/`audit.py`, and
available as a general-purpose helper, `exit_codes.worst(*codes)`, for
future callers that need to combine exit codes from multiple sources:

```
INTERNAL_ERROR > COMMAND_EXECUTION_FAILURE > REPO_NOT_FOUND
  > INVALID_CONFIG > BLOCKED > WARNINGS_PRESENT > SUCCESS
```

Per-command specifics:

- **`doctor`**: a failing `config-validity` check forces `INVALID_CONFIG`
  (3) even if other checks also failed, since a broken config is the more
  actionable root cause; any other `fail` status forces
  `COMMAND_EXECUTION_FAILURE` (5); otherwise any `warning` forces
  `WARNINGS_PRESENT` (1); otherwise `SUCCESS` (0). A repository not being
  found is a `warning`, not a `fail`, for `doctor` specifically (see
  `docs/cli-architecture.md`).
- **`status`**: `REPO_NOT_FOUND` (4) immediately if no repository is
  found; `COMMAND_EXECUTION_FAILURE` (5) if git itself is unavailable;
  otherwise `WARNINGS_PRESENT` (1) if any check warned (e.g. an invalid
  `.agent/CURRENT_STATE.json`); otherwise `SUCCESS` (0). A dirty working
  tree is not itself a warning — being dirty is normal, not a fault.
- **`audit`**: `REPO_NOT_FOUND` (4) / `COMMAND_EXECUTION_FAILURE` (5) as
  above; otherwise `BLOCKED` (2) if any check is `blocked` (a secret
  match or a dangerous filename in working-tree changes); otherwise
  `WARNINGS_PRESENT` (1) if any check warned; otherwise `SUCCESS` (0).
- **`test --full`** (Phase 2C): identical precedence to `test --targeted`
  (see `docs/targeted-testing.md`) - a selected command that didn't
  complete successfully forces `COMMAND_EXECUTION_FAILURE` (5); otherwise
  a plan warning (missing test-runner evidence, unsupported stack) forces
  `WARNINGS_PRESENT` (1); otherwise `SUCCESS` (0).
- **`release-check`** (Phase 2C): the *first* real caller of
  `exit_codes.worst(*codes)` - combines its own gates' worst code with
  `run_doctor`'s, `run_audit`'s, and `run_full_test`'s exit codes via that
  helper. Because `worst()`'s documented precedence ranks
  `COMMAND_EXECUTION_FAILURE` above `BLOCKED`, a repository with *both* a
  blocked secret finding *and* a failing test suite in the same run
  reports exit code 5, not 2 - the JSON output's `data.blocking_checks`
  still lists both, so nothing is hidden; only the single integer can't
  represent two simultaneous "worst" reasons. See `docs/release-check.md`
  for the full explanation. This precedence was defined in Phase 2A and
  is unchanged by Phase 2C - `release-check` simply exercises it for the
  first time.
- **`checkpoint` / `handoff`** (Phase 2C): `REPO_NOT_FOUND` (4) /
  `COMMAND_EXECUTION_FAILURE` (5, git unavailable) as above; a
  `COMMAND_EXECUTION_FAILURE` (5) also results if the atomic write itself
  fails (disk full, permission denied - a `fail`-status
  `checkpoint-write`/`handoff-write` check, mirroring how `doctor`'s
  `log-dir-writable` check handles a write failure); otherwise
  `WARNINGS_PRESENT` (1) if the previous `.agent/CURRENT_STATE.json` had
  an unsupported `schema_version` (recovered with fresh narrative
  defaults, not blocked); otherwise `SUCCESS` (0). A dirty working tree
  is not itself a warning for either command - like `status`, capturing
  the working-tree shape *is* the point, not a fault to flag. See
  `docs/checkpoint-and-handoff.md`.
- **`process-list` / `cleanup`** (this checkpoint): `REPO_NOT_FOUND` (4) /
  `COMMAND_EXECUTION_FAILURE` (5, git unavailable or a registry-write
  failure) as above; otherwise `WARNINGS_PRESENT` (1) if there's a
  process-discovery limitation (e.g. non-Windows, PowerShell/CIM
  unavailable), a registry-schema warning, or (`cleanup` only) at least
  one reported termination/stale-record candidate or action - including
  a graceful termination that didn't stop the process within the bounded
  wait (an honest, expected outcome since cleanup never escalates to
  force-kill); otherwise `SUCCESS` (0). Neither command ever returns
  `BLOCKED` - that code is reserved for `audit`/`release-check`'s secret
  and dangerous-filename findings. See `docs/process-list-and-cleanup.md`.
- **`resume-context`**: `REPO_NOT_FOUND` (4) / `COMMAND_EXECUTION_FAILURE`
  (5, git unavailable) as above; `WARNINGS_PRESENT` (1) if no
  `.agent/CURRENT_STATE.json` exists yet, its schema is unsupported, or
  (in principle - guarded by a fixed test) the bounded document exceeds
  its own size ceiling; otherwise `SUCCESS` (0). Read-only - never
  returns `BLOCKED`. See `docs/context-efficiency.md`.
- **`init`**: `BLOCKED` (2) if the target is the read-only reference
  repository (or beneath it), or if preflight finds any managed path in
  conflict (existing content that isn't recognizably ForgeOps-owned, or
  malformed/schema-incompatible `.agent/CURRENT_STATE.json`) - in either
  case nothing is written, and `--dry-run` returns the same code a real
  run would rather than always reporting `SUCCESS`; `REPO_NOT_FOUND` (4)
  if the target path doesn't exist or isn't a directory (reused rather
  than a new code, since the underlying condition - "no usable target at
  this path" - is the same shape); `COMMAND_EXECUTION_FAILURE` (5) if an
  atomic write genuinely fails partway through (this run's new paths are
  rolled back first); otherwise `SUCCESS` (0) for a clean or idempotent
  initialization. Never returns `WARNINGS_PRESENT` or `INVALID_CONFIG` -
  see `docs/project-init.md`.

## Stability guarantee

These seven codes and their meanings are the stable contract established
in Phase 2A. Phase 2B and Phase 2C both reused this table rather than
introducing new numeric meanings - if a new distinction is needed, it
should be expressed as a new `checks[]` status detail or `data` field,
not a new exit code, to keep automation (hooks, CI, other tooling)
written against these seven codes forward-compatible.
