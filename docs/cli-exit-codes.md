# CLI Exit Codes

Defined once in `forgeops/core/exit_codes.py` and used identically by
every command — no command defines its own ad-hoc codes.

| Code | Name | Meaning |
|---|---|---|
| 0 | `SUCCESS` | No warnings, no blocking findings. |
| 1 | `WARNINGS_PRESENT` | Completed with one or more non-blocking warnings (e.g. an optional tool missing, a state file present but invalid). |
| 2 | `BLOCKED` | A blocking safety finding was reported — currently only `forgeops audit` can return this (a likely secret, or a dangerous filename pattern in working-tree changes). |
| 3 | `INVALID_CONFIG` | `[tool.forgeops]` in `pyproject.toml` exists but is malformed (bad TOML, wrong value type, non-table section). |
| 4 | `REPO_NOT_FOUND` | No git repository could be discovered at or above the given path (or an explicit `--repo` path doesn't exist). |
| 5 | `COMMAND_EXECUTION_FAILURE` | A required external command failed or was unavailable — currently only `git` itself. |
| 6 | `INTERNAL_ERROR` | Reserved for an unexpected internal ForgeOps error. Not yet raised anywhere in Phase 2A (see "Known limitations" in `docs/cli-architecture.md`) — defined now so later phases don't need to renumber anything. |

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

## Stability guarantee

These seven codes and their meanings are the stable contract for Phase
2A. New commands in Phase 2B must reuse this table rather than
introducing new numeric meanings — if a new distinction is needed, it
should be expressed as a new `checks[]` status detail or `data` field,
not a new exit code, to keep automation (hooks, CI, other tooling)
written against these seven codes forward-compatible.
