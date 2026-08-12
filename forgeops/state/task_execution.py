"""Deterministic core logic behind `forgeops task run` (see
`forgeops/cli/task.py`): a read-only preflight-plan builder and a single
mutating apply step, following the same plan/apply split every other
mutating ForgeOps command uses.

This is the first ForgeOps command that spawns a real, long-running
external process (a `claude` or `codex` CLI invocation) rather than only
reading or writing JSON. See docs/agent-execution.md for the full
precondition/mechanics contract and `.agent/DECISIONS.md` for the
reasoning behind the choices this module encodes:

- Execution is synchronous and bounded by `--timeout` rather than a
  background process - nothing else in ForgeOps uses `Popen`, and
  `"claude"`/`"codex"` are already hard-coded into
  `forgeops.state.runtime_registry.NEVER_MANAGED_CATEGORIES`, so a
  blocking call needs no process-registry entry at all.
- An assigned, live worktree is a hard precondition - this command never
  runs an agent against the primary checkout.
- `status` only ever moves to a non-terminal value (`active` while
  running, `validation_pending` on a clean exit, `blocked` on a
  non-zero exit or a timeout) - `completed`/`failed` remain `task
  close`'s exclusive privilege.
- The prompt (built from `SPEC.md`) is secret-scanned before launch, a
  match blocks the run outright; captured stdout/stderr, in contrast,
  is always redacted-and-persisted via `LogWriter`, never used to
  refuse recording that a run happened.
- The executable is injectable (`executable_override`) so tests never
  have to shell out to a real, costly, non-deterministic `claude`/
  `codex` CLI."""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from forgeops.core.subprocess_utils import run as run_subprocess
from forgeops.core.timestamps import Clock, iso_now
from forgeops.reporting.logs import LogWriter
from forgeops.security.secret_scan import scan_text
from forgeops.state.agent_registry import AgentRecord, KIND_CLAUDE, KIND_CODEX
from forgeops.state.task_ownership import (
    _lookup_agent,
    _lookup_task,
    _lookup_worktree,
)
from forgeops.state.task_registry import (
    APPROVAL_STATE_APPROVED,
    EXECUTION_EVENT_COMPLETED,
    EXECUTION_EVENT_FAILED,
    EXECUTION_EVENT_STARTED,
    EXECUTION_EVENT_TIMED_OUT,
    SPEC_MD_FILENAME,
    STATUS_ACTIVE,
    STATUS_BLOCKED,
    STATUS_VALIDATION_PENDING,
    TERMINAL_STATUSES,
    ExecutionEvent,
    TaskIndexDocument,
    TaskIndexRecord,
    TaskRecord,
    load_index as load_task_index,
    save_index as save_task_index,
    save_task_record,
    task_dir_for,
    validate_actor,
)
from forgeops.state.worktree_create import ConflictItem
from forgeops.state.worktree_registry import WorktreeRecord

# Bounded generously (real coding work takes minutes, not the 15s
# default `forgeops.core.subprocess_utils.run` uses elsewhere) but still
# bounded - unbounded/background execution is explicitly out of scope,
# see the module docstring.
DEFAULT_RUN_TIMEOUT_SECONDS = 3600.0

# Only these two agent kinds have a defined executable this checkpoint -
# `specialist`/`rocky` are recognized identities elsewhere in ForgeOps
# but have no launchable command yet (see docs/agents.md kinds and
# docs/agent-execution.md "Explicit non-goals").
EXECUTABLE_BY_KIND: dict[str, str] = {
    KIND_CLAUDE: "claude",
    KIND_CODEX: "codex",
}


@dataclass(frozen=True)
class TaskRunPlan:
    repo_root: Path
    task_id: str
    actor: str
    timeout: float
    task_record: TaskRecord | None
    agent_record: AgentRecord | None
    worktree_record: WorktreeRecord | None
    resolved_executable: str | None
    prompt: str | None
    conflicts: tuple[ConflictItem, ...] = field(default_factory=tuple)

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)


def build_task_run_plan(
    repo_root: Path, task_id: str, actor: str, timeout: float = DEFAULT_RUN_TIMEOUT_SECONDS,
) -> TaskRunPlan:
    """Read-only preflight, shared by `--dry-run` and a real run, and
    re-run verbatim by `apply_task_run` immediately before mutating to
    close the preflight/apply TOCTOU gap. Never writes anything, never
    launches a process - `agent-executable-not-found` only checks
    `shutil.which`, the same resolution `subprocess_utils.run` performs
    internally, so a dry-run honestly reports whether a real run could
    even start."""
    conflicts: list[ConflictItem] = []

    actor_error = validate_actor(actor)
    if actor_error is not None:
        conflicts.append(ConflictItem("invalid-actor", actor_error))
    else:
        findings, _ = scan_text(actor, relative_path=f".agent/tasks/{task_id}/__actor__")
        if findings:
            categories = ", ".join(sorted({f.category for f in findings}))
            conflicts.append(ConflictItem(
                "actor-secret-detected",
                f"actor appears to contain secret-shaped content ({categories}) - refusing to persist it",
            ))

    task_lookup = _lookup_task(repo_root, task_id)
    conflicts.extend(task_lookup.conflicts)

    agent_record: AgentRecord | None = None
    worktree_record: WorktreeRecord | None = None
    resolved_executable: str | None = None
    prompt: str | None = None

    if task_lookup.record is not None:
        record = task_lookup.record

        if record.status in TERMINAL_STATUSES:
            conflicts.append(ConflictItem(
                "task-already-terminal",
                f"task status is '{record.status}' - a terminal task cannot be run",
            ))

        if record.approval_state != APPROVAL_STATE_APPROVED:
            conflicts.append(ConflictItem(
                "task-not-approved",
                f"task approval_state is '{record.approval_state}' - only an approved task can be run",
            ))

        if record.agent_id is None:
            conflicts.append(ConflictItem(
                "task-no-agent-assigned",
                f"task '{task_id}' has no assigned agent - nothing to run it",
            ))
        else:
            agent_lookup = _lookup_agent(repo_root, record.agent_id)
            conflicts.extend(agent_lookup.conflicts)
            agent_record = agent_lookup.record
            if agent_record is not None:
                if agent_record.kind not in EXECUTABLE_BY_KIND:
                    conflicts.append(ConflictItem(
                        "unsupported-agent-kind",
                        f"agent '{agent_record.agent_id}' has kind '{agent_record.kind}' - only "
                        f"{sorted(EXECUTABLE_BY_KIND)} can be run this checkpoint",
                    ))
                else:
                    executable_name = EXECUTABLE_BY_KIND[agent_record.kind]
                    resolved = shutil.which(executable_name)
                    if resolved is None:
                        conflicts.append(ConflictItem(
                            "agent-executable-not-found",
                            f"'{executable_name}' was not found on PATH - cannot launch agent '{agent_record.agent_id}'",
                        ))
                    else:
                        resolved_executable = resolved

        if record.worktree_id is None:
            conflicts.append(ConflictItem(
                "task-no-worktree-assigned",
                f"task '{task_id}' has no assigned worktree - running against the primary checkout is refused",
            ))
        else:
            worktree_lookup = _lookup_worktree(repo_root, record.worktree_id)
            conflicts.extend(worktree_lookup.conflicts)
            worktree_record = worktree_lookup.record

        spec_path = task_dir_for(repo_root, task_id) / SPEC_MD_FILENAME
        try:
            prompt = spec_path.read_text(encoding="utf-8")
        except OSError as exc:
            conflicts.append(ConflictItem("spec-unreadable", f"could not read {SPEC_MD_FILENAME}: {exc}"))
        else:
            findings, _ = scan_text(prompt, relative_path=f".agent/tasks/{task_id}/{SPEC_MD_FILENAME}")
            if findings:
                categories = ", ".join(sorted({f.category for f in findings}))
                conflicts.append(ConflictItem(
                    "spec-secret-detected",
                    f"SPEC.md appears to contain secret-shaped content ({categories}) - refusing to launch",
                ))

    return TaskRunPlan(
        repo_root=repo_root, task_id=task_id, actor=actor, timeout=timeout,
        task_record=task_lookup.record, agent_record=agent_record, worktree_record=worktree_record,
        resolved_executable=resolved_executable, prompt=prompt, conflicts=tuple(conflicts),
    )


@dataclass(frozen=True)
class TaskRunOutcome:
    ok: bool
    task_json_written: bool
    index_written: bool
    index_error: str | None
    exit_code: int | None
    timed_out: bool
    log_path: str | None
    partial_state: dict | None


def apply_task_run(
    repo_root: Path,
    plan: TaskRunPlan,
    clock: Clock | None = None,
    executable_override: list[str] | None = None,
    write_log: bool = True,
) -> TaskRunOutcome:
    """Launches a real subprocess. `TASK.json` is written before the
    process starts (`status=active`, a "started" `execution_history`
    event) and again after it returns (final status per the outcome, a
    second `execution_history` event) - two separate atomic writes, not
    one atomic pair, because the subprocess call in between can take up
    to `plan.timeout` seconds and nothing should be "held open" across
    it the way `task_ownership.py`/`task_approval.py` hold a rollback
    window open across two near-instant writes. `TASK_INDEX.json` is
    updated last, bookkeeping only - its own failure is never a call
    failure, mirroring every other ownership-style apply in this
    codebase."""
    assert plan.task_record is not None

    fresh_plan = build_task_run_plan(plan.repo_root, plan.task_id, plan.actor, plan.timeout)
    if fresh_plan.has_conflict:
        return TaskRunOutcome(
            ok=False, task_json_written=False, index_written=False, index_error=None,
            exit_code=None, timed_out=False, log_path=None,
            partial_state={
                "reason": "task state changed between preflight and apply - refusing to proceed",
                "conflicts": [{"key": c.key, "message": c.message} for c in fresh_plan.conflicts],
            },
        )
    assert fresh_plan.agent_record is not None and fresh_plan.worktree_record is not None
    assert fresh_plan.resolved_executable is not None and fresh_plan.prompt is not None

    task_dir = task_dir_for(repo_root, plan.task_id)
    original_task_record = fresh_plan.task_record
    started_at = iso_now(clock)

    started_event = ExecutionEvent(event=EXECUTION_EVENT_STARTED, actor=plan.actor, timestamp=started_at)
    # dataclasses.replace, not `TaskRecord(**{**original.to_dict(), ...})`:
    # to_dict() serializes approval_history/execution_history to plain
    # dicts, and unpacking that back into the constructor would silently
    # store those plain dicts in place of ApprovalEvent/ExecutionEvent
    # objects for every field this call doesn't explicitly override -
    # harmless only as long as the untouched list happens to be empty.
    active_record = replace(
        original_task_record,
        status=STATUS_ACTIVE,
        execution_history=[*original_task_record.execution_history, started_event],
        updated_at=started_at,
    )
    try:
        save_task_record(task_dir, active_record)
    except OSError as exc:
        return TaskRunOutcome(
            ok=False, task_json_written=False, index_written=False, index_error=None,
            exit_code=None, timed_out=False, log_path=None,
            partial_state={"error": str(exc), "task_json_written": False},
        )

    args = executable_override or [fresh_plan.resolved_executable, "-p", fresh_plan.prompt]
    worktree_path = Path(fresh_plan.worktree_record.path)
    start_perf = time.monotonic()
    proc_result = run_subprocess(args, cwd=worktree_path, timeout=plan.timeout)
    duration_seconds = time.monotonic() - start_perf

    log_path: str | None = None
    if write_log:
        log_content = (
            f"forgeops task run {plan.task_id}\n"
            f"args: {list(proc_result.args)}\n"
            f"cwd: {worktree_path}\n"
            f"returncode: {proc_result.returncode}\n"
            f"timed_out: {proc_result.timed_out}\n"
            f"error: {proc_result.error}\n"
            f"--- stdout ---\n{proc_result.stdout}\n"
            f"--- stderr ---\n{proc_result.stderr}\n"
        )
        written_path = LogWriter(repo_root, "task-run", clock=clock).write(f"{plan.task_id}.log", log_content)
        log_path = str(written_path)

    if proc_result.timed_out:
        final_status = STATUS_BLOCKED
        final_event_name = EXECUTION_EVENT_TIMED_OUT
    elif proc_result.ok:
        final_status = STATUS_VALIDATION_PENDING
        final_event_name = EXECUTION_EVENT_COMPLETED
    else:
        final_status = STATUS_BLOCKED
        final_event_name = EXECUTION_EVENT_FAILED

    finished_at = iso_now(clock)
    final_event = ExecutionEvent(
        event=final_event_name, actor=plan.actor, timestamp=finished_at,
        exit_code=proc_result.returncode, timed_out=proc_result.timed_out,
        duration_seconds=duration_seconds, log_path=log_path,
    )
    final_record = replace(
        active_record,
        status=final_status,
        execution_history=[*active_record.execution_history, final_event],
        updated_at=finished_at,
    )
    try:
        save_task_record(task_dir, final_record)
    except OSError as exc:
        return TaskRunOutcome(
            ok=False, task_json_written=True, index_written=False, index_error=None,
            exit_code=proc_result.returncode, timed_out=proc_result.timed_out, log_path=log_path,
            partial_state={
                "error": str(exc), "task_json_written": False,
                "note": "the subprocess already ran to completion; only the final TASK.json write failed",
            },
        )

    index_written, index_error = _update_task_index_execution(
        repo_root, plan.task_id, final_status, final_event_name, finished_at,
    )
    return TaskRunOutcome(
        ok=True, task_json_written=True, index_written=index_written, index_error=index_error,
        exit_code=proc_result.returncode, timed_out=proc_result.timed_out, log_path=log_path,
        partial_state=None,
    )


def _update_task_index_execution(
    repo_root: Path, task_id: str, status: str, last_execution_status: str, now: str,
) -> tuple[bool, str | None]:
    fresh_index = load_task_index(repo_root)
    if fresh_index.warning is not None:
        return False, fresh_index.warning
    updated_records = [
        TaskIndexRecord(**{
            **r.to_dict(), "status": status, "last_execution_status": last_execution_status, "updated_at": now,
        }) if r.task_id == task_id else r
        for r in fresh_index.records
    ]
    try:
        save_task_index(repo_root, TaskIndexDocument(
            schema_version=fresh_index.schema_version, next_task_number=fresh_index.next_task_number,
            records=updated_records,
        ))
        return True, None
    except OSError as exc:
        return False, str(exc)
