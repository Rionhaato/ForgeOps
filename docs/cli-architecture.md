# CLI Architecture (Phase 2A + 2B + 2C + Phase 2C checkpoint/handoff)

## Module layout

```
forgeops/
  __main__.py          python -m forgeops entry point
  cli/
    __init__.py         argparse setup, subcommand registration, main(), top-level exception boundary
    render.py            shared human-readable rendering primitives
    doctor.py             forgeops doctor
    status.py             forgeops status
    audit.py               forgeops audit
    changed.py              forgeops changed
    test.py                  forgeops test --targeted | --full
    release_check.py          forgeops release-check (aggregates doctor/audit/test --full)
    checkpoint.py              forgeops checkpoint (writes .agent/CURRENT_STATE.json)
    handoff.py                 forgeops handoff (writes .agent/HANDOFF.md, derived from checkpoint data)
    process_list.py             forgeops process-list (read-only process discovery/classification)
    cleanup.py                   forgeops cleanup (conservative, dry-run-by-default process/registry cleanup)
    resume_context.py           forgeops resume-context (read-only, bounded-size compact resume summary)
    init.py                      forgeops init [PATH] (safe, deterministic project bootstrap - see docs/project-init.md)
    worktree.py                   forgeops worktree list | create | remove (safe Git worktree inspection/creation/removal - see docs/worktrees.md)
    task.py                       forgeops task create | show | list | validate | close | assign | unassign (persistent Task Specification Engine + ownership - see docs/tasks.md)
  core/
    paths.py             repo-root discovery, path normalization, read-only reference repository protection
    git.py                read-only git-state inspection (status entries, renames, ahead/behind, ...)
    config.py             [tool.forgeops] loading (pyproject.toml)
    result.py             Check / CommandResult structured model
    exit_codes.py         shared exit-code constants
    subprocess_utils.py   safe subprocess execution (incl. Windows .cmd-shim resolution)
    timestamps.py         deterministic, injectable-clock timestamps
    governance.py          small constants mirroring CLAUDE.md sections 8/11 (shared by handoff + resume-context)
  detectors/
    tree_scan.py          single bounded filesystem walk -> TreeScan
    stack.py               stack detection from tree_scan evidence
    changed.py              changed-file classification (area/technology/broad-impact)
    processes.py             Windows-native OS process discovery (PowerShell/CIM + netstat), no psutil
    process_association.py    pure managed/associated/uncertain/unrelated/stale_record classification
  security/
    redact.py              text redaction (used for raw log persistence)
    secret_scan.py          secret-pattern scanning, redacted findings, scoped allow-secret marker
    dangerous_files.py      dangerous-filename pattern matching
  state/
    schema.py              lightweight .agent/CURRENT_STATE.json validation, supported schema-version set
    checkpoint.py            pure, deterministic CURRENT_STATE.json document builder (shared by checkpoint + handoff)
    atomic_write.py          atomic same-filesystem temp-file-then-replace text writer
    runtime_registry.py       .agent/runtime/PROCESS_REGISTRY.json load/save (shared by process-list + cleanup)
    resume_context.py          pure, bounded-size compact resume-context document builder (shared by resume-context CLI)
    project_init.py             pure preflight-plan builder, content renderers, and rollback-on-failure writer for `forgeops init` (see docs/project-init.md)
    worktree_registry.py         .agent/runtime/WORKTREE_REGISTRY.json load/save (mirrors runtime_registry.py's shape/safety)
    worktree_create.py            pure preflight-plan builder and single mutating writer for `forgeops worktree create` (see docs/worktrees.md)
    worktree_remove.py            pure preflight-plan builder and single mutating apply step for `forgeops worktree remove` (see docs/worktrees.md)
    task_registry.py               TASK_INDEX.json / TASK.json / VALIDATION.json schema, constants, atomic load/save (see docs/tasks.md)
    task_spec.py                    SPEC.md/RESULT.md rendering/parsing, safe spec-file/acceptance-file/result-file ingestion
    task_create.py                   pure preflight-plan builder and single mutating writer for `forgeops task create`
    task_validate.py                  read-only structural/consistency check shared by `task validate` and `task close`'s own preflight
    task_close.py                      pure preflight-plan builder and single mutating apply step for `forgeops task close`
    task_ownership.py                   pure preflight-plan builders and mutating apply steps for `forgeops task assign`/`forgeops task unassign` (see docs/tasks.md "Ownership")
  worktrees/
    naming.py                NAME validation, deterministic branch-name/managed-root/path derivation
    git_worktree.py            git worktree list/add plumbing - tolerant porcelain parsing, no shell interpolation
  reporting/
    logs.py                 logs/<command>/<timestamp>/ persistence
  testing/
    planner.py              deterministic targeted- and full-test plan construction
    executor.py              sequential, bounded-timeout test-plan execution
```

Every command module (`doctor.py`/`status.py`/`audit.py`/`changed.py`)
exposes two functions: `run_<command>(repo_arg, cwd=None, clock=None,
write_log=True, ...) -> CommandResult` and `render_human(result) -> str`.
`test.py` follows the same shape but exposes **two** run functions,
`run_test_targeted` and `run_full_test` (Phase 2C), sharing one
`render_human` since both produce the same `plan`/`execution` data
shape. `release_check.py` (Phase 2C) follows the same shape too
(`run_release_check`, `render_human`), but its `run_*` function itself
calls `run_doctor`/`run_audit`/`run_full_test` internally rather than
re-deriving their checks - see `docs/release-check.md`. `checkpoint.py`
and `handoff.py` (Phase 2C) also follow the same shape (`run_checkpoint`/
`run_handoff`, `render_human`, both accepting an additional `dry_run`
keyword) - see `docs/checkpoint-and-handoff.md` for their state-writing
behavior, atomic-write guarantee, and the consistency model between the
two. `process_list.py` (read-only) and `cleanup.py` (mutating, one of two
exceptions in this toolkit) round out the same shape - `run_process_list`,
`run_cleanup` (accepting `execute: bool = False`, defaulting to a dry
run), `render_human` - see `docs/process-list-and-cleanup.md` for the
classification model, PID-reuse protection, and cleanup's strict safety
invariants. `init.py` (the other mutating exception; `run_init`,
accepting `dry_run: bool = False`, `render_human`) resolves its own
target directly from a `path` argument rather than via
`resolve_repo_root` - see "Target resolution" in `docs/project-init.md`
for why it deliberately does not follow the shared repo-discovery
convention every other command uses. `worktree.py` (the third mutating
exception, and the first three-subcommand module) exposes
`run_worktree_list` (read-only), `run_worktree_create` (accepting
`dry_run: bool = False`, `branch: str | None = None`,
`base: str | None = None`), and `run_worktree_remove` (accepting
`dry_run: bool = False`, `confirm: bool = False`,
`delete_branch: bool = False`), sharing one `render_human` that
dispatches on `result.command` (`"worktree-list"` / `"worktree-create"`
/ `"worktree-remove"`) - see docs/worktrees.md for the managed root,
branch-naming rule, conflict model, registry, and removal
eligibility/confirmation model this implements. `task.py` (the fourth
mutating exception - `task create`/`task close`/`task assign`/`task
unassign` mutate, `task show`/`task list`/`task validate` don't - and
the first seven-subcommand module) exposes `run_task_create`,
`run_task_show`, `run_task_list`, `run_task_validate`,
`run_task_close`, `run_task_assign` (accepting `dry_run: bool = False`
only - no `--confirm`, mirroring `task create`/`worktree create`'s own
additive, easily-reversed shape rather than a destructive one), and
`run_task_unassign` (accepting `dry_run: bool = False`, `confirm: bool
= False`, mirroring `task close`/`worktree remove` instead), sharing
one `render_human` dispatching on `result.command` (`"task-create"` /
`"task-show"` / `"task-list"` / `"task-validate"` / `"task-close"` /
`"task-assign"` / `"task-unassign"`). All seven share a single
repo-level gate (`_repo_level_block`) that every other worktree/init
command doesn't need: a protected-reference-repo check identical to the
others, plus a new one - the target must already be an initialized
ForgeOps project (`.agent/CURRENT_STATE.json` present and schema-valid)
- since the Task Specification Engine has nowhere safe to persist state
otherwise. See `docs/tasks.md` for the task directory contract, ID
generation, lifecycle, validation-artifact model, closure's
confirmation/atomicity model, and the ownership layer's one-to-one
model shared with `forgeops/state/worktree_registry.py`. `run_*` never touches `sys.argv`,
`print`, or `sys.exit` — it's a pure function over its arguments, which is
what makes it directly unit-testable (see `tests/integration/test_cli_*.py`)
without spawning a subprocess. `main()` in `forgeops/cli/__init__.py` is
the only place that parses `sys.argv`, calls `print`, and returns an exit
code - and, as of Phase 2B, the only place that catches an unexpected
exception (see "Top-level exception handling" below).

`main()` dispatches to `run_*`/`render_human` via `getattr(module,
"run_<command>")` at call time, not a dict of pre-bound function
references built once at import time. This matters for testability: a
dict literal like `{"doctor": doctor_cmd.run_doctor}` captures the
function object doctor_cmd.run_doctor pointed to *at import time* -
`monkeypatch.setattr("forgeops.cli.doctor.run_doctor", fake)` in a test
then has no effect, because the dict still holds the original reference.
`getattr(doctor_cmd, "run_doctor")` performed fresh on every call always
sees the current attribute, patched or not. This was a real bug caught
while writing Phase 2B's exception-handling tests - see
`docs/phase2b-validation.md`.

## Entry points

Both work identically:
- `forgeops <command>` — the `pyproject.toml` `[project.scripts]` console
  script (`forgeops = "forgeops.cli:main"`).
- `python -m forgeops <command>` — via `forgeops/__main__.py`.

Both accept `--json` (structured output) and `--repo <path>` (explicit
repository path; otherwise discovered from the current working directory,
walking upward for a `.git` entry — works from a nested directory and
handles paths containing spaces natively via `pathlib`). `init` is the
one exception: `forgeops init [PATH]` takes a positional target path
instead of `--repo`, and deliberately never walks upward for `.git` —
see "Target resolution" in `docs/project-init.md`.

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

## Top-level exception handling (Phase 2B, Part 5)

`main()` wraps the `run_fn(...)` + `render_fn(result)` call pair in a
single `try/except Exception`. Expected user/configuration errors
(missing repository, invalid `[tool.forgeops]`, missing git) never reach
this boundary at all - they're already caught *inside* each `run_*`
function and returned as a normal `CommandResult` with the correct exit
code (`REPO_NOT_FOUND`, `INVALID_CONFIG`, `COMMAND_EXECUTION_FAILURE`).
Only a genuinely unexpected exception (a real bug) is caught here:

- Exit code `INTERNAL_ERROR` (6), always.
- A concise, redacted message on stderr (human mode) or in a `message`
  field (`--json` mode) - never a raw traceback by default.
- A sanitized diagnostic log at `logs/<command>/<timestamp>/internal_error.log`
  (the full traceback, passed through `forgeops.security.redact.redact_text`
  before being written) when a repository root can be determined at all;
  if not (e.g. `--repo` pointed at a path with no `.git`), the message
  says so explicitly rather than silently skipping.
- `--debug` (a flag on the top-level parser, so it must precede the
  subcommand: `forgeops --debug doctor`) or the `FORGEOPS_DEBUG`
  environment variable (any value other than empty/`0`/`false`) re-raises
  the real exception instead, for local debugging.

## Known limitations

- `forgeops audit`'s secret scan is line-based pattern matching, not a
  parser — see `docs/audit-security-model.md` for what this does and
  doesn't catch.
- `agents`, `approvals`, `validate-config`, `install`, `uninstall` are
  registered in the argument parser (so `forgeops <name> --help` works
  and produces a clean error) but not implemented — invoking any of them
  prints "not yet implemented" to stderr and exits 1. `forgeops test`
  without `--targeted` or `--full` behaves the same way. `checkpoint`,
  `handoff`, `process-list`, `cleanup`, `init`, `worktree list`/
  `worktree create`/`worktree remove`, and `task create`/`task show`/
  `task list`/`task validate`/`task close`/`task assign`/`task
  unassign` are now implemented (see `docs/checkpoint-and-handoff.md`,
  `docs/process-list-and-cleanup.md`, `docs/project-init.md`,
  `docs/worktrees.md`, `docs/tasks.md`). `worktree prune`, bulk/forced
  removal, merge orchestration, agent assignment/execution, parallel
  task routing, automatic worktree creation, and an approvals workflow
  all remain unimplemented - see `docs/worktrees.md` and `docs/tasks.md`
  "Explicit non-goals". See `docs/phase2c-validation.md`
  for prior recommended-next-scope notes.
- Process discovery (`forgeops/detectors/processes.py`) is Windows-only
  today - `forgeops process-list`/`forgeops cleanup` report a clear
  `WARNINGS_PRESENT` limitation and an empty process list on other
  platforms rather than erroring or guessing.
- No ForgeOps command in this checkpoint *writes* a new
  `PROCESS_REGISTRY.json` record during normal operation - there is no
  launch/init mechanism yet that starts a tracked background process.
  `process-list`'s heuristic discovery does not depend on the registry
  being populated.
