"""Tests for forgeops/state/task_execution.py: the read-only preflight
plan builder and mutating apply step behind `forgeops task run`. See
docs/agent-execution.md for the full precondition/mechanics contract.

No test here ever shells out to a real `claude`/`codex` CLI -
`executable_override` (a `[sys.executable, "-c", "<script>"]` list) is
used throughout to simulate success/failure/timeout deterministically."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from forgeops.state.agent_register import apply_agent_register, build_agent_register_plan
from forgeops.state.agent_registry import (
    KIND_CLAUDE,
    KIND_CODEX,
    KIND_SPECIALIST,
    STATUS_DISABLED,
    load_agent_registry,
    save_agent_registry,
)
from forgeops.state.task_approval import (
    apply_task_approve,
    apply_task_request_approval,
    build_task_approve_plan,
    build_task_request_approval_plan,
)
from forgeops.state.task_create import apply_task_create, build_task_create_plan
from forgeops.state.task_execution import (
    EXECUTABLE_BY_KIND,
    apply_task_run,
    build_task_run_plan,
)
from forgeops.state.task_ownership import (
    apply_task_assign,
    apply_task_assign_agent,
    build_task_assign_agent_plan,
    build_task_assign_plan,
)
from forgeops.state.task_registry import (
    EXECUTION_EVENT_COMPLETED,
    EXECUTION_EVENT_FAILED,
    EXECUTION_EVENT_STARTED,
    EXECUTION_EVENT_TIMED_OUT,
    STATUS_ACTIVE,
    STATUS_BLOCKED,
    STATUS_VALIDATION_PENDING,
    load_index as load_task_index,
    load_task_record,
    save_task_record,
    task_dir_for,
)
from forgeops.state.worktree_create import apply_worktree_create, build_worktree_create_plan
from forgeops.state.worktree_registry import STATUS_REMOVED, load_registry as load_worktree_registry, save_registry

MAX_BYTES = 2 * 1024 * 1024


def _create_task(repo_root, title="My Task"):
    plan = build_task_create_plan(repo_root, title, None, None, MAX_BYTES)
    outcome = apply_task_create(repo_root, plan)
    assert outcome.ok is True
    return plan.task_id


def _create_worktree(repo_root, name="demo"):
    plan = build_worktree_create_plan(repo_root, name, None, None)
    outcome = apply_worktree_create(repo_root, plan)
    assert outcome.ok is True
    return name


def _register_agent(repo_root, agent_id="claude-primary", kind=KIND_CLAUDE):
    plan = build_agent_register_plan(repo_root, agent_id, kind, None)
    outcome = apply_agent_register(repo_root, plan)
    assert outcome.ok is True
    return agent_id


def _ready_task(repo_root, *, actor="joshua", agent_kind=KIND_CLAUDE):
    """Full precondition chain for `task run`: created, assigned a real
    worktree, assigned a registered agent, and approved."""
    task_id = _create_task(repo_root)
    worktree_name = _create_worktree(repo_root)
    apply_task_assign(repo_root, build_task_assign_plan(repo_root, task_id, worktree_name))
    agent_id = _register_agent(repo_root, kind=agent_kind)
    apply_task_assign_agent(repo_root, build_task_assign_agent_plan(repo_root, task_id, agent_id))
    apply_task_request_approval(repo_root, build_task_request_approval_plan(repo_root, task_id, actor))
    apply_task_approve(repo_root, build_task_approve_plan(repo_root, task_id, actor))
    return task_id, worktree_name, agent_id


def _ok_script() -> list[str]:
    return [sys.executable, "-c", "import sys; sys.exit(0)"]


def _fail_script() -> list[str]:
    return [sys.executable, "-c", "import sys; sys.exit(1)"]


def _sleep_script(seconds: float = 5.0) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


# --- preflight: happy path -----------------------------------------------------


def test_run_clean_plan_has_no_conflicts(initialized_repo):
    task_id, worktree_name, agent_id = _ready_task(initialized_repo)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    assert plan.has_conflict is False
    assert plan.resolved_executable is not None
    assert plan.prompt is not None
    assert plan.worktree_record.name == worktree_name
    assert plan.agent_record.agent_id == agent_id


def test_run_plan_never_writes_anything(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    before = set(initialized_repo.rglob("*"))
    build_task_run_plan(initialized_repo, task_id, "joshua")
    after = set(initialized_repo.rglob("*"))
    assert before == after


def test_run_missing_task(initialized_repo):
    plan = build_task_run_plan(initialized_repo, "task-9999", "joshua")
    assert any(c.key == "task-not-found" for c in plan.conflicts)


# --- preflight: actor -----------------------------------------------------------


def test_run_invalid_actor_empty(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    plan = build_task_run_plan(initialized_repo, task_id, "")
    assert any(c.key == "invalid-actor" for c in plan.conflicts)


def test_run_secret_like_actor_refused(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    plan = build_task_run_plan(initialized_repo, task_id, "AKIAABCDEFGHIJKLMNOP")  # forgeops:allow-secret
    assert any(c.key == "actor-secret-detected" for c in plan.conflicts)


# --- preflight: task status / approval -------------------------------------------


def test_run_terminal_task_refused(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    record = load_task_record(task_dir).record
    save_task_record(task_dir, type(record)(**{**record.__dict__, "status": "completed"}))
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "task-already-terminal" for c in plan.conflicts)


def test_run_not_approved_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    worktree_name = _create_worktree(initialized_repo)
    apply_task_assign(initialized_repo, build_task_assign_plan(initialized_repo, task_id, worktree_name))
    agent_id = _register_agent(initialized_repo)
    apply_task_assign_agent(initialized_repo, build_task_assign_agent_plan(initialized_repo, task_id, agent_id))
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "task-not-approved" for c in plan.conflicts)


# --- preflight: agent ownership / eligibility -------------------------------------


def test_run_no_agent_assigned_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    worktree_name = _create_worktree(initialized_repo)
    apply_task_assign(initialized_repo, build_task_assign_plan(initialized_repo, task_id, worktree_name))
    apply_task_request_approval(initialized_repo, build_task_request_approval_plan(initialized_repo, task_id, "joshua"))
    apply_task_approve(initialized_repo, build_task_approve_plan(initialized_repo, task_id, "joshua"))
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "task-no-agent-assigned" for c in plan.conflicts)


def test_run_disabled_agent_refused(initialized_repo):
    task_id, _, agent_id = _ready_task(initialized_repo)
    registry = load_agent_registry(initialized_repo)
    updated = [
        type(r)(**{**r.__dict__, "status": STATUS_DISABLED}) if r.agent_id == agent_id else r
        for r in registry.records
    ]
    save_agent_registry(initialized_repo, type(registry)(schema_version=registry.schema_version, records=updated))
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "agent-disabled" for c in plan.conflicts)


def test_run_unsupported_agent_kind_refused(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo, agent_kind=KIND_SPECIALIST)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "unsupported-agent-kind" for c in plan.conflicts)


def test_run_executable_not_found_refused(initialized_repo, monkeypatch):
    task_id, _, _ = _ready_task(initialized_repo)
    monkeypatch.setattr("forgeops.state.task_execution.shutil.which", lambda name: None)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "agent-executable-not-found" for c in plan.conflicts)
    assert plan.resolved_executable is None


# --- preflight: worktree ownership / eligibility ----------------------------------


def test_run_no_worktree_assigned_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    agent_id = _register_agent(initialized_repo)
    apply_task_assign_agent(initialized_repo, build_task_assign_agent_plan(initialized_repo, task_id, agent_id))
    apply_task_request_approval(initialized_repo, build_task_request_approval_plan(initialized_repo, task_id, "joshua"))
    apply_task_approve(initialized_repo, build_task_approve_plan(initialized_repo, task_id, "joshua"))
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "task-no-worktree-assigned" for c in plan.conflicts)


def test_run_removed_worktree_refused(initialized_repo):
    task_id, worktree_name, _ = _ready_task(initialized_repo)
    registry = load_worktree_registry(initialized_repo)
    updated = [
        type(r)(**{**r.__dict__, "status": STATUS_REMOVED}) if r.name == worktree_name else r
        for r in registry.records
    ]
    save_registry(initialized_repo, type(registry)(schema_version=registry.schema_version, records=updated))
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "worktree-removed" for c in plan.conflicts)


# --- preflight: SPEC.md secret scan ------------------------------------------------


def test_run_spec_secret_detected_refused(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    spec_path = task_dir_for(initialized_repo, task_id) / "SPEC.md"
    spec_path.write_text("key is AKIAABCDEFGHIJKLMNOP\n", encoding="utf-8")  # forgeops:allow-secret
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "spec-secret-detected" for c in plan.conflicts)


# --- apply: success / failure / timeout --------------------------------------------


def test_apply_run_success_transitions_to_validation_pending(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    outcome = apply_task_run(initialized_repo, plan, executable_override=_ok_script(), write_log=False)
    assert outcome.ok is True
    assert outcome.exit_code == 0
    assert outcome.timed_out is False

    record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert record.status == STATUS_VALIDATION_PENDING
    assert [e.event for e in record.execution_history] == [EXECUTION_EVENT_STARTED, EXECUTION_EVENT_COMPLETED]
    assert record.execution_history[0].actor == "joshua"
    assert record.execution_history[1].exit_code == 0

    index = load_task_index(initialized_repo)
    idx_record = next(r for r in index.records if r.task_id == task_id)
    assert idx_record.status == STATUS_VALIDATION_PENDING
    assert idx_record.last_execution_status == EXECUTION_EVENT_COMPLETED


def test_apply_run_nonzero_exit_transitions_to_blocked(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    outcome = apply_task_run(initialized_repo, plan, executable_override=_fail_script(), write_log=False)
    assert outcome.ok is True
    assert outcome.exit_code == 1
    assert outcome.timed_out is False

    record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert record.status == STATUS_BLOCKED
    assert [e.event for e in record.execution_history] == [EXECUTION_EVENT_STARTED, EXECUTION_EVENT_FAILED]


def test_apply_run_timeout_transitions_to_blocked(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua", timeout=0.2)
    outcome = apply_task_run(initialized_repo, plan, executable_override=_sleep_script(5.0), write_log=False)
    assert outcome.ok is True
    assert outcome.timed_out is True

    record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert record.status == STATUS_BLOCKED
    assert [e.event for e in record.execution_history] == [EXECUTION_EVENT_STARTED, EXECUTION_EVENT_TIMED_OUT]


def test_apply_run_writes_log_file_when_enabled(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    outcome = apply_task_run(initialized_repo, plan, executable_override=_ok_script(), write_log=True)
    assert outcome.log_path is not None
    from pathlib import Path
    assert Path(outcome.log_path).is_file()


def test_apply_run_intermediate_status_is_active_during_execution(initialized_repo):
    """The subprocess call is synchronous, so this can't observe the
    in-flight state directly - instead it proves the first write really
    happened by checking history/exit-code fields the *second* write
    could only have produced by starting from that first record."""
    task_id, _, _ = _ready_task(initialized_repo)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    outcome = apply_task_run(initialized_repo, plan, executable_override=_ok_script(), write_log=False)
    assert outcome.ok is True
    record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    started, completed = record.execution_history
    assert started.event == EXECUTION_EVENT_STARTED
    assert started.timestamp <= completed.timestamp


# --- apply: TOCTOU and write-failure paths ------------------------------------------


def test_apply_run_toctou_state_changed_refuses(initialized_repo):
    task_id, _, _ = _ready_task(initialized_repo)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    assert not plan.has_conflict
    # Invalidate the plan by terminating the task before apply runs.
    task_dir = task_dir_for(initialized_repo, task_id)
    record = load_task_record(task_dir).record
    save_task_record(task_dir, type(record)(**{**record.__dict__, "status": "cancelled"}))

    outcome = apply_task_run(initialized_repo, plan, executable_override=_ok_script(), write_log=False)
    assert outcome.ok is False
    assert outcome.partial_state is not None
    assert outcome.exit_code is None


def test_apply_run_started_write_failure(initialized_repo, monkeypatch):
    task_id, _, _ = _ready_task(initialized_repo)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    monkeypatch.setattr(
        "forgeops.state.task_execution.save_task_record",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_run(initialized_repo, plan, executable_override=_ok_script(), write_log=False)
    assert outcome.ok is False
    assert outcome.task_json_written is False


def test_apply_run_index_write_failure_is_not_a_call_failure(initialized_repo, monkeypatch):
    """Mirrors `apply_task_assign`'s ownership convention (not
    `apply_task_approve`'s stricter pairing): the process already ran,
    so a failed index update is reported via `index_error`, never
    rolled back or treated as `outcome.ok is False`."""
    task_id, _, _ = _ready_task(initialized_repo)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    monkeypatch.setattr(
        "forgeops.state.task_execution.save_task_index",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_run(initialized_repo, plan, executable_override=_ok_script(), write_log=False)
    assert outcome.ok is True
    assert outcome.index_written is False
    assert outcome.index_error is not None
    record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert record.status == STATUS_VALIDATION_PENDING


# --- apply: uses the worktree, not the primary checkout -----------------------------


def test_apply_run_uses_worktree_path_as_cwd(initialized_repo):
    task_id, worktree_name, _ = _ready_task(initialized_repo)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    marker_script = [
        sys.executable, "-c",
        "import os, sys; open('marker.txt', 'w').write(os.getcwd()); sys.exit(0)",
    ]
    outcome = apply_task_run(initialized_repo, plan, executable_override=marker_script, write_log=False)
    assert outcome.ok is True
    worktree_path = plan.worktree_record.path
    from pathlib import Path
    marker = Path(worktree_path) / "marker.txt"
    assert marker.is_file()
    assert Path(marker.read_text(encoding="utf-8")).resolve() == Path(worktree_path).resolve()


# --- apply: claude-kind launches are isolated from the operator's Claude Code config ---


_ENV_MARKER_SCRIPT = [
    sys.executable, "-c",
    "import os; open('marker.txt', 'w').write(os.environ.get('CLAUDE_CONFIG_DIR', ''))",
]


def test_apply_run_claude_kind_does_not_inherit_operators_claude_config_dir(initialized_repo, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "operators-real-config-dir-with-hooks")
    task_id, worktree_name, _ = _ready_task(initialized_repo, agent_kind=KIND_CLAUDE)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    outcome = apply_task_run(initialized_repo, plan, executable_override=_ENV_MARKER_SCRIPT, write_log=False)
    assert outcome.ok is True
    seen = (Path(plan.worktree_record.path) / "marker.txt").read_text(encoding="utf-8")
    assert seen != ""
    assert seen != "operators-real-config-dir-with-hooks"


def test_apply_run_isolated_claude_config_dir_is_removed_after_run(initialized_repo):
    task_id, worktree_name, _ = _ready_task(initialized_repo, agent_kind=KIND_CLAUDE)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    outcome = apply_task_run(initialized_repo, plan, executable_override=_ENV_MARKER_SCRIPT, write_log=False)
    assert outcome.ok is True
    isolated_dir = (Path(plan.worktree_record.path) / "marker.txt").read_text(encoding="utf-8")
    assert isolated_dir != ""
    assert not Path(isolated_dir).exists()


_CREDENTIAL_MARKER_SCRIPT = [
    sys.executable, "-c",
    "import os, json as j; d = os.environ.get('CLAUDE_CONFIG_DIR', ''); "
    "cp = os.path.join(d, '.credentials.json'); sp = os.path.join(d, 'settings.json'); "
    "cred = open(cp, encoding='utf-8').read() if os.path.isfile(cp) else None; "
    "open('marker.txt', 'w', encoding='utf-8').write(j.dumps({'dir': d, 'credentials': cred, 'settings_present': os.path.isfile(sp)}))",
]


def test_apply_run_claude_kind_copies_credentials_but_not_hooks_into_isolated_dir(
    initialized_repo, monkeypatch, tmp_path,
):
    """FO-010 real-launch validation found a bare empty CLAUDE_CONFIG_DIR
    logs the launched process out entirely (`claude` exited 1, "Not
    logged in"). The fix copies just the on-disk session credential
    into the isolated dir - this pins that settings.json (hooks) is
    still deliberately excluded, only auth is preserved."""
    real_config_dir = tmp_path / "operators-real-claude-config"
    real_config_dir.mkdir()
    (real_config_dir / "settings.json").write_text(
        '{"hooks": {"UserPromptSubmit": [{"command": "block-the-prompt"}]}}', encoding="utf-8",
    )
    (real_config_dir / ".credentials.json").write_text('{"token": "fixture-credential"}', encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(real_config_dir))

    task_id, worktree_name, _ = _ready_task(initialized_repo, agent_kind=KIND_CLAUDE)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    outcome = apply_task_run(initialized_repo, plan, executable_override=_CREDENTIAL_MARKER_SCRIPT, write_log=False)
    assert outcome.ok is True

    seen = json.loads((Path(plan.worktree_record.path) / "marker.txt").read_text(encoding="utf-8"))
    assert seen["credentials"] == '{"token": "fixture-credential"}'
    assert seen["settings_present"] is False
    assert seen["dir"] != str(real_config_dir)


def test_apply_run_codex_kind_is_not_touched_by_claude_config_isolation(initialized_repo, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "operators-real-config-dir-with-hooks")
    # codex isn't necessarily on this test runner's PATH; only the
    # preflight's executable-resolvability check needs to pass here -
    # executable_override replaces the actual launch args below.
    monkeypatch.setattr("forgeops.state.task_execution.shutil.which", lambda name: name)
    task_id, worktree_name, _ = _ready_task(initialized_repo, agent_kind=KIND_CODEX)
    plan = build_task_run_plan(initialized_repo, task_id, "joshua")
    outcome = apply_task_run(initialized_repo, plan, executable_override=_ENV_MARKER_SCRIPT, write_log=False)
    assert outcome.ok is True
    seen = (Path(plan.worktree_record.path) / "marker.txt").read_text(encoding="utf-8")
    assert seen == "operators-real-config-dir-with-hooks"
