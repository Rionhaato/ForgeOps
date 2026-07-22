# CLI Architecture (Phase 2A)

## Module layout

```
forgeops/
  __main__.py          python -m forgeops entry point
  cli/
    __init__.py         argparse setup, subcommand registration, main()
    render.py            shared human-readable rendering primitives
    doctor.py             forgeops doctor
    status.py             forgeops status
    audit.py               forgeops audit
  core/
    paths.py             repo-root discovery, path normalization
    git.py                read-only git-state inspection
    config.py             [tool.forgeops] loading (pyproject.toml)
    result.py             Check / CommandResult structured model
    exit_codes.py         shared exit-code constants
    subprocess_utils.py   safe subprocess execution
    timestamps.py         deterministic, injectable-clock timestamps
  detectors/
    tree_scan.py          single bounded filesystem walk -> TreeScan
    stack.py               stack detection from tree_scan evidence
  security/
    redact.py              text redaction (used for raw log persistence)
    secret_scan.py          secret-pattern scanning, redacted findings
    dangerous_files.py      dangerous-filename pattern matching
  state/
    schema.py              lightweight .agent/CURRENT_STATE.json validation
  reporting/
    logs.py                 logs/<command>/<timestamp>/ persistence
```

Every command module (`doctor.py`/`status.py`/`audit.py`) exposes two
functions: `run_<command>(repo_arg, cwd=None, clock=None, write_log=True)
-> CommandResult` and `render_human(result) -> str`. `run_*` never touches
`sys.argv`, `print`, or `sys.exit` — it's a pure function over its
arguments, which is what makes it directly unit-testable (see
`tests/integration/test_cli_*.py`) without spawning a subprocess. `main()`
in `forgeops/cli/__init__.py` is the only place that parses `sys.argv`,
calls `print`, and returns an exit code.

## Entry points

Both work identically:
- `forgeops <command>` — the `pyproject.toml` `[project.scripts]` console
  script (`forgeops = "forgeops.cli:main"`).
- `python -m forgeops <command>` — via `forgeops/__main__.py`.

Both accept `--json` (structured output) and `--repo <path>` (explicit
repository path; otherwise discovered from the current working directory,
walking upward for a `.git` entry — works from a nested directory and
handles paths containing spaces natively via `pathlib`).

## The `CommandResult` / `Check` model

Every command returns the same shape (`forgeops/core/result.py`):

```json
{
  "command": "audit",
  "schema_version": 1,
  "generated_at": "2026-07-22T00:00:00Z",
  "repo_root": "C:\\path\\to\\repo",
  "exit_code": 0,
  "summary": "3 informational, 13 pass (exit 0)",
  "checks": [
    {"id": "secret-scan", "label": "Secret-pattern scan", "status": "pass", "message": "...", "detail": {}}
  ],
  "data": {}
}
```

`checks[].status` is one of `pass | warning | blocked | fail |
informational`. Every check has a stable, kebab-case `id` (e.g.
`git-available`, `repo-discovery`, `secret-scan`) — these ids are part of
the contract other tooling (hooks, later phases) can depend on; command
modules use them internally to compute the overall `exit_code`, and tests
assert against them directly (`ids = {c.id: c for c in result.checks}`)
rather than against display text, so renamed labels don't break tests.

`data` carries command-specific structured detail (`status`'s full repo
facts; `audit`'s `files_scanned_for_secrets` count). `doctor` currently
leaves `data` empty — everything it reports fits naturally as checks.

## Why one filesystem walk (`detectors/tree_scan.py`)

`forgeops audit` needs to classify a repository's files into many
categories (env files, db files, media files, browser-state files,
dependency manifests, instruction files, stack markers, oversized files,
generated-artifact directories, nested git repos, and the set of files
safe to open for secret-pattern scanning). Doing this as one bounded walk
that prunes vendor/generated directories on sight — recording their
presence without descending into them — means a repo with a large
`node_modules/` is only ever touched once, not once per category. See
`docs/audit-security-model.md` for exactly which categories are excluded
from content-reading versus which are scanned.

## Design choices worth naming explicitly

- **`run_*` functions never raise for expected failure modes** (missing
  git, no repository found, invalid config) — they return a
  `CommandResult` with an appropriate `exit_code` and a `fail`-status
  check explaining why. Unexpected exceptions are not caught anywhere in
  Phase 2A; an uncaught exception is a real bug, and hiding it behind
  `INTERNAL_ERROR` without a traceback would make debugging harder, not
  safer. (`INTERNAL_ERROR` exists in `exit_codes.py` for a future phase
  to use once there's a top-level exception boundary that logs the
  traceback before returning it — see "Known limitations" below.)
- **`doctor` treats "no repository found" as a warning, not a failure.**
  `doctor` is meant to answer "is this machine's ForgeOps environment
  healthy," which is a meaningful question to ask even outside any git
  repository (e.g. before running `forgeops init`). `status` and `audit`,
  by contrast, are inherently repo-scoped and return `REPO_NOT_FOUND`
  immediately if no repository is found.
- **Command modules import `git_version` directly** (`from
  forgeops.core.git import git_version`) rather than calling it through a
  module-qualified reference, specifically so tests can
  `monkeypatch.setattr("forgeops.cli.doctor.git_version", ...)` per
  command without reaching into `forgeops.core.git` globally and
  affecting other commands' tests.

## Known limitations (Phase 2A)

- No top-level exception handler in `main()` — an unexpected internal
  error currently produces a Python traceback on stderr rather than a
  clean `INTERNAL_ERROR` (6) exit. Acceptable for Phase 2A (three
  well-tested, read-only commands); revisit once mutating commands
  (Phase 2B) raise the stakes of an uncaught exception mid-operation.
- `forgeops audit`'s secret scan is line-based pattern matching, not a
  parser — see `docs/audit-security-model.md` for what this does and
  doesn't catch.
- `forgeops init`, `checkpoint`, `handoff`, `changed`, `test`,
  `release-check`, `process-list`, `cleanup`, `worktree`, `agents`,
  `approvals`, `validate-config`, `install`, `uninstall` are registered in
  the argument parser (so `forgeops <name> --help` works and produces a
  clean error) but not implemented — invoking any of them prints "not yet
  implemented" to stderr and exits 1. See `docs/phase2a-validation.md` for
  the exact recommended Phase 2B scope.
