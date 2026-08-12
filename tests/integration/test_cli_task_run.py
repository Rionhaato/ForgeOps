"""Integration tests for `forgeops task run` (forgeops/cli/task.py). See
docs/agent-execution.md for the precondition chain, confirmation/dry-run
model, and status transitions this exercises end-to-end through the CLI
entry points.

No test here shells out to a real `claude`/`codex` CLI -
`executable_override` (a `[sys.executable, "-c", "<script>"]` list) is
passed directly to `run_task_run` throughout, exactly the same seam the
unit tests in tests/unit/test_task_execution.py use."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from forgeops.cli.agent import run_agent_register
from forgeops.cli.task import (
    render_human,
    run_task_approve,
    run_task_assign,
    run_task_assign_agent,
    run_task_create,
    run_task_request_approval,
    run_task_run,
    run_task_show,
)
from forgeops.cli.worktree import run_worktree_create
from forgeops.core import exit_codes
from forgeops.state.task_registry import (
    STATUS_BLOCKED,
    STATUS_VALIDATION_PENDING,
    load_index,
    load_task_record,
    task_dir_for,
)

MAX_BYTES = 2 * 1024 * 1024


def _ok_script() -> list[str]:
    return [sys.executable, "-c", "import sys; sys.exit(0)"]


def _fail_script() -> list[str]:
    return [sys.executable, "-c", "import sys; sys.exit(1)"]


def _create_task(repo, title="My Task"):
    result = run_task_create(title, str(repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    return result.data["task_id"]


def _create_worktree(repo, name="demo"):
    result = run_worktree_create(name, str(repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    return name


def _register_agent(repo, agent_id="claude-primary", kind="claude"):
    result = run_agent_register(agent_id, kind, str(repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    return agent_id


def _ready_task(repo, *, actor="joshua"):
    """Full CLI-level precondition chain for `task run`: created,
    assigned a real worktree, assigned a registered agent, and
    approved."""
    task_id = _create_task(repo)
    worktree_name = _create_worktree(repo)
    assert run_task_assign(task_id, worktree_name, str(repo), write_log=False).exit_code == exit_codes.SUCCESS
    agent_id = _register_agent(repo)
    assert run_task_assign_agent(task_id, agent_id, str(repo), write_log=False).exit_code == exit_codes.SUCCESS
    assert run_task_request_approval(task_id, str(repo), write_log=False, actor=actor).exit_code == exit_codes.SUCCESS
    result = run_task_approve(task_id, str(repo), write_log=False, actor=actor, confirm=True)
    assert result.exit_code == exit_codes.SUCCESS
    return task_id, worktree_name, agent_id


# --- preflight / gating -----------------------------------------------------------


def test_run_confirmation_required(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    before = set(initialized_repo.rglob("*"))
    result = run_task_run(task_id, str(initialized_repo), write_log=False, actor="joshua")
    after = set(initialized_repo.rglob("*"))
    assert result.exit_code == exit_codes.BLOCKED
    assert result.data["action"] == "confirmation_required"
    assert before == after


def test_run_dry_run_zero_mutation(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    before = set(initialized_repo.rglob("*"))
    result = run_task_run(task_id, str(initialized_repo), write_log=False, actor="joshua", dry_run=True)
    after = set(initialized_repo.rglob("*"))
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["action"] == "would_run"
    assert before == after


def test_run_not_approved_blocked(initialized_repo):
    task_id = _create_task(initialized_repo)
    worktree_name = _create_worktree(initialized_repo)
    run_task_assign(task_id, worktree_name, str(initialized_repo), write_log=False)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    result = run_task_run(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-not-approved" for c in result.data["conflicts"])


def test_run_no_worktree_blocked(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    run_task_assign_agent(task_id, agent_id, str(initialized_repo), write_log=False)
    run_task_request_approval(task_id, str(initialized_repo), write_log=False, actor="joshua")
    run_task_approve(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    result = run_task_run(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-no-worktree-assigned" for c in result.data["conflicts"])


def test_run_missing_task(initialized_repo):
    result = run_task_run("task-9999", str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-not-found" for c in result.data["conflicts"])


def test_run_invalid_actor_blocked(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    result = run_task_run(task_id, str(initialized_repo), write_log=False, actor="", confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-actor" for c in result.data["conflicts"])


# --- confirmed run: success / failure ------------------------------------------------


def test_run_confirmed_success(initialized_repo):
    task_id, worktree_name, agent_id = _ready_task(initialized_repo)
    result = run_task_run(
        task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True,
        executable_override=_ok_script(),
    )
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["action"] == "run_completed"
    assert result.data["exit_code"] == 0
    assert result.data["timed_out"] is False

    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.status == STATUS_VALIDATION_PENDING
    assert [e.event for e in task_record.execution_history] == ["started", "completed"]

    index = load_index(initialized_repo)
    idx_record = next(r for r in index.records if r.task_id == task_id)
    assert idx_record.status == STATUS_VALIDATION_PENDING
    assert idx_record.last_execution_status == "completed"


def test_run_confirmed_nonzero_exit_reports_warnings_present(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    result = run_task_run(
        task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True,
        executable_override=_fail_script(),
    )
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    assert result.data["action"] == "run_did_not_succeed"
    assert result.data["exit_code"] == 1

    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.status == STATUS_BLOCKED
    assert [e.event for e in task_record.execution_history] == ["started", "failed"]


def test_run_writes_log_file_when_write_log_true(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    result = run_task_run(
        task_id, str(initialized_repo), write_log=True, actor="joshua", confirm=True,
        executable_override=_ok_script(),
    )
    assert result.exit_code == exit_codes.SUCCESS
    log_path = result.data["log_path"]
    assert log_path is not None
    assert Path(log_path).is_file()
    assert Path(log_path).is_relative_to(initialized_repo / "logs")


def test_run_redacts_secret_shaped_output_in_log(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    leaky_script = [
        sys.executable, "-c",
        "print('Authorization: Bearer sk-thisisnotarealsecretvalue00000000000'); "  # forgeops:allow-secret
        "import sys; sys.exit(0)",
    ]
    result = run_task_run(
        task_id, str(initialized_repo), write_log=True, actor="joshua", confirm=True,
        executable_override=leaky_script,
    )
    log_text = Path(result.data["log_path"]).read_text(encoding="utf-8")
    assert "sk-thisisnotarealsecretvalue00000000000" not in log_text  # forgeops:allow-secret
    assert "[REDACTED]" in log_text


def test_run_human_json_agree(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    result = run_task_run(
        task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True,
        executable_override=_ok_script(),
    )
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops task run" in rendered
    assert payload["data"]["exit_code"] == 0


def test_show_reflects_execution_history(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    run_task_run(
        task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True,
        executable_override=_ok_script(),
    )
    result = run_task_show(task_id, str(initialized_repo), write_log=False)
    assert result.data["task"]["status"] == STATUS_VALIDATION_PENDING
    assert result.data["task"]["execution_history"][-1]["event"] == "completed"


# --- scope: a run only ever touches its own task's files + one log dir ----------------


def _snapshot(root: Path) -> dict[str, str]:
    snapshot = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/"):
            continue
        try:
            snapshot[rel] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            snapshot[rel] = "<unreadable>"
    return snapshot


def test_run_scope_is_limited_to_its_own_task_and_one_log_dir(initialized_repo):
    task_id, worktree_name, _ = _ready_task(initialized_repo)
    before = _snapshot(initialized_repo)

    result = run_task_run(
        task_id, str(initialized_repo), write_log=True, actor="joshua", confirm=True,
        executable_override=_ok_script(),
    )
    assert result.exit_code == exit_codes.SUCCESS

    after = _snapshot(initialized_repo)
    changed = {k for k in after if before.get(k) != after.get(k)} | (before.keys() - after.keys())

    task_prefix = f".agent/tasks/{task_id}/"
    allowed_prefixes = (task_prefix, ".agent/tasks/TASK_INDEX.json", "logs/task-run/")
    unexpected = [c for c in changed if not c.startswith(allowed_prefixes) and c != ".agent/tasks/TASK_INDEX.json"]
    assert unexpected == [], f"unexpected files touched by task run: {unexpected}"
