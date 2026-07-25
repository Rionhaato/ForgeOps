"""Integration tests for `forgeops task request-approval|approve|reject|
cancel-approval` (forgeops/cli/task.py). See docs/approvals.md for the
state machine, confirmation/dry-run model, and security boundaries this
exercises end-to-end through the CLI entry points."""
from __future__ import annotations

import json
import subprocess

from forgeops.cli.task import (
    render_human,
    run_task_approve,
    run_task_cancel_approval,
    run_task_close,
    run_task_create,
    run_task_list,
    run_task_reject,
    run_task_request_approval,
    run_task_show,
    run_task_validate,
)
from forgeops.core import exit_codes
from forgeops.state.task_registry import (
    VALIDATION_STATUS_PASSED,
    load_index,
    load_task_record,
    load_validation_record,
    save_validation_record,
    task_dir_for,
)


def _create_task(repo, title="My Task"):
    result = run_task_create(title, str(repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    return result.data["task_id"]


def _fill_spec_and_close(repo, task_id, tmp_path):
    task_dir = task_dir_for(repo, task_id)
    (task_dir / "SPEC.md").write_text(
        "# SPEC\n\n## Objective\n\nfoo\n\n## In Scope\n\nfoo\n\n## Out of Scope\n\nfoo\n\n"
        "## Constraints\n\nfoo\n\n## Acceptance Criteria\n\n- works\n\n"
        "## Required Validation\n\n- pytest\n\n## Stop Boundary\n\nfoo\n",
        encoding="utf-8",
    )
    load = load_validation_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "status": VALIDATION_STATUS_PASSED})
    save_validation_record(task_dir, tampered)
    result_file = tmp_path / "result.md"
    result_file.write_text("## Outcome\n\ndone\n", encoding="utf-8")
    result = run_task_close(task_id, str(repo), write_log=False, confirm=True, result_file=str(result_file))
    assert result.exit_code == exit_codes.SUCCESS


def _request(repo, task_id, actor="joshua", reason=None):
    result = run_task_request_approval(task_id, str(repo), write_log=False, actor=actor, reason=reason)
    assert result.exit_code == exit_codes.SUCCESS
    return result


# --- request-approval --------------------------------------------------------------


def test_request_approval_success(initialized_repo):
    task_id = _create_task(initialized_repo)
    result = run_task_request_approval(task_id, str(initialized_repo), write_log=False, actor="joshua")
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["action"] == "approval_state_changed"
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.approval_state == "pending"


def test_request_approval_dry_run_zero_mutation(initialized_repo):
    task_id = _create_task(initialized_repo)
    before = set(initialized_repo.rglob("*"))
    result = run_task_request_approval(task_id, str(initialized_repo), write_log=False, actor="joshua", dry_run=True)
    after = set(initialized_repo.rglob("*"))
    assert result.data["action"] == "would_request_approval"
    assert before == after


def test_request_approval_no_confirm_needed(initialized_repo):
    task_id = _create_task(initialized_repo)
    result = run_task_request_approval(task_id, str(initialized_repo), write_log=False, actor="joshua")
    assert result.exit_code == exit_codes.SUCCESS  # additive, no --confirm required


def test_request_approval_invalid_actor_blocked(initialized_repo):
    task_id = _create_task(initialized_repo)
    before = set(initialized_repo.rglob("*"))
    result = run_task_request_approval(task_id, str(initialized_repo), write_log=False, actor="")
    after = set(initialized_repo.rglob("*"))
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-actor" for c in result.data["conflicts"])
    assert before == after


def test_request_approval_secret_like_actor_blocked(initialized_repo):
    task_id = _create_task(initialized_repo)
    result = run_task_request_approval(task_id, str(initialized_repo), write_log=False, actor="AKIAABCDEFGHIJKLMNOP")
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "actor-secret-detected" for c in result.data["conflicts"])


def test_request_approval_oversized_reason_blocked(initialized_repo):
    task_id = _create_task(initialized_repo)
    result = run_task_request_approval(task_id, str(initialized_repo), write_log=False, actor="joshua", reason="x" * 3000)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-reason" for c in result.data["conflicts"])


def test_request_approval_missing_task(initialized_repo):
    result = run_task_request_approval("task-9999", str(initialized_repo), write_log=False, actor="joshua")
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-not-found" for c in result.data["conflicts"])


def test_request_approval_terminal_task_refused(initialized_repo, tmp_path):
    task_id = _create_task(initialized_repo)
    _fill_spec_and_close(initialized_repo, task_id, tmp_path)
    result = run_task_request_approval(task_id, str(initialized_repo), write_log=False, actor="joshua")
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-already-terminal" for c in result.data["conflicts"])


def test_request_approval_malformed_task(initialized_repo):
    task_id = _create_task(initialized_repo)
    (task_dir_for(initialized_repo, task_id) / "TASK.json").write_text("{ not valid", encoding="utf-8")
    result = run_task_request_approval(task_id, str(initialized_repo), write_log=False, actor="joshua")
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-json-malformed" for c in result.data["conflicts"])


def test_request_approval_duplicate_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    result = run_task_request_approval(task_id, str(initialized_repo), write_log=False, actor="joshua")
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-approval-transition" for c in result.data["conflicts"])


def test_request_approval_spacey_paths(spacey_initialized_repo):
    task_id = _create_task(spacey_initialized_repo)
    result = run_task_request_approval(task_id, str(spacey_initialized_repo), write_log=False, actor="joshua")
    assert result.exit_code == exit_codes.SUCCESS


def test_request_approval_human_json_agree(initialized_repo):
    task_id = _create_task(initialized_repo)
    result = run_task_request_approval(task_id, str(initialized_repo), write_log=False, actor="joshua")
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops task request-approval" in rendered
    assert payload["data"]["new_approval_state"] == "pending"


def test_request_approval_does_not_touch_status_or_ownership(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.status == "draft"
    assert task_record.worktree_id is None
    assert task_record.agent_id is None


def test_request_approval_atomic_rollback_on_index_failure(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    monkeypatch.setattr(
        "forgeops.state.task_approval.save_task_index",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    result = run_task_request_approval(task_id, str(initialized_repo), write_log=False, actor="joshua")
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.approval_state == "not_requested"
    assert task_record.approval_history == []


# --- approve --------------------------------------------------------------------


def test_approve_confirmation_required(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    before = set(initialized_repo.rglob("*"))
    result = run_task_approve(task_id, str(initialized_repo), write_log=False, actor="joshua")
    after = set(initialized_repo.rglob("*"))
    assert result.exit_code == exit_codes.BLOCKED
    assert result.data["action"] == "confirmation_required"
    assert before == after


def test_approve_dry_run_zero_mutation(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    before = set(initialized_repo.rglob("*"))
    result = run_task_approve(task_id, str(initialized_repo), write_log=False, actor="joshua", dry_run=True)
    after = set(initialized_repo.rglob("*"))
    assert result.data["action"] == "would_approve"
    assert before == after


def test_approve_confirmed_success(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    result = run_task_approve(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    assert result.exit_code == exit_codes.SUCCESS
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.approval_state == "approved"
    assert [e.action for e in task_record.approval_history] == ["requested", "approved"]
    assert task_record.status == "draft"
    assert task_record.worktree_id is None
    assert task_record.agent_id is None


def test_approve_index_reflects_new_state(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    run_task_approve(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    index = load_index(initialized_repo)
    idx_record = next(r for r in index.records if r.task_id == task_id)
    assert idx_record.approval_state == "approved"


def test_approve_without_prior_request_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    result = run_task_approve(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-approval-transition" for c in result.data["conflicts"])


def test_approve_already_approved_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    run_task_approve(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    result = run_task_approve(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-approval-transition" for c in result.data["conflicts"])


def test_approve_terminal_task_refused(initialized_repo, tmp_path):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    _fill_spec_and_close(initialized_repo, task_id, tmp_path)
    result = run_task_approve(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "task-already-terminal" for c in result.data["conflicts"])


def test_approve_invalid_actor_blocked(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    result = run_task_approve(task_id, str(initialized_repo), write_log=False, actor="has spaces!!", confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-actor" for c in result.data["conflicts"])


def test_approve_human_json_agree(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    result = run_task_approve(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops task approve" in rendered
    assert payload["data"]["new_approval_state"] == "approved"


def test_approve_atomic_rollback_on_index_failure(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    monkeypatch.setattr(
        "forgeops.state.task_approval.save_task_index",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    result = run_task_approve(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.approval_state == "pending"  # rolled back


def test_approve_spacey_paths(spacey_initialized_repo):
    task_id = _create_task(spacey_initialized_repo)
    _request(spacey_initialized_repo, task_id)
    result = run_task_approve(task_id, str(spacey_initialized_repo), write_log=False, actor="joshua", confirm=True)
    assert result.exit_code == exit_codes.SUCCESS


# --- reject ---------------------------------------------------------------------


def test_reject_requires_reason(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    result = run_task_reject(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "reason-required" for c in result.data["conflicts"])


def test_reject_confirmation_required(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    before = set(initialized_repo.rglob("*"))
    result = run_task_reject(task_id, str(initialized_repo), write_log=False, actor="joshua", reason="not ready")
    after = set(initialized_repo.rglob("*"))
    assert result.exit_code == exit_codes.BLOCKED
    assert result.data["action"] == "confirmation_required"
    assert before == after


def test_reject_dry_run_zero_mutation(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    before = set(initialized_repo.rglob("*"))
    result = run_task_reject(task_id, str(initialized_repo), write_log=False, actor="joshua", reason="not ready", dry_run=True)
    after = set(initialized_repo.rglob("*"))
    assert result.data["action"] == "would_reject"
    assert before == after


def test_reject_confirmed_success_with_reason(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    result = run_task_reject(task_id, str(initialized_repo), write_log=False, actor="joshua", reason="not ready", confirm=True)
    assert result.exit_code == exit_codes.SUCCESS
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.approval_state == "rejected"
    assert task_record.approval_history[-1].reason == "not ready"


def test_reject_secret_like_reason_blocked(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    result = run_task_reject(
        task_id, str(initialized_repo), write_log=False, actor="joshua",
        reason="the key is AKIAABCDEFGHIJKLMNOP", confirm=True,
    )
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "reason-secret-detected" for c in result.data["conflicts"])


def test_reject_human_json_agree(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    result = run_task_reject(task_id, str(initialized_repo), write_log=False, actor="joshua", reason="no", confirm=True)
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops task reject" in rendered
    assert payload["data"]["new_approval_state"] == "rejected"


# --- cancel-approval --------------------------------------------------------------


def test_cancel_approval_confirmation_required(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    before = set(initialized_repo.rglob("*"))
    result = run_task_cancel_approval(task_id, str(initialized_repo), write_log=False, actor="joshua")
    after = set(initialized_repo.rglob("*"))
    assert result.exit_code == exit_codes.BLOCKED
    assert result.data["action"] == "confirmation_required"
    assert before == after


def test_cancel_approval_dry_run_zero_mutation(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    before = set(initialized_repo.rglob("*"))
    result = run_task_cancel_approval(task_id, str(initialized_repo), write_log=False, actor="joshua", dry_run=True)
    after = set(initialized_repo.rglob("*"))
    assert result.data["action"] == "would_cancel_approval"
    assert before == after


def test_cancel_approval_confirmed_success(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    result = run_task_cancel_approval(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    assert result.exit_code == exit_codes.SUCCESS
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.approval_state == "not_requested"
    assert [e.action for e in task_record.approval_history] == ["requested", "cancelled"]


def test_cancel_approval_from_not_requested_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    result = run_task_cancel_approval(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-approval-transition" for c in result.data["conflicts"])


def test_cancel_approval_from_approved_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    run_task_approve(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    result = run_task_cancel_approval(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "invalid-approval-transition" for c in result.data["conflicts"])


def test_cancel_approval_human_json_agree(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    result = run_task_cancel_approval(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops task cancel-approval" in rendered
    assert payload["data"]["new_approval_state"] == "not_requested"


def test_full_cycle_reject_then_request_then_cancel(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    run_task_reject(task_id, str(initialized_repo), write_log=False, actor="joshua", reason="no", confirm=True)
    _request(initialized_repo, task_id)
    result = run_task_cancel_approval(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    assert result.exit_code == exit_codes.SUCCESS
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.approval_state == "not_requested"
    assert [e.action for e in task_record.approval_history] == ["requested", "rejected", "requested", "cancelled"]


# --- show / list / validate reflect approval state --------------------------------


def test_show_reflects_approval_state_and_last_event(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id, reason="please review")
    result = run_task_show(task_id, str(initialized_repo), write_log=False)
    assert result.data["task"]["approval_state"] == "pending"
    assert result.data["task"]["approval_history"][-1]["action"] == "requested"
    rendered = render_human(result)
    assert "approval_state: pending" in rendered
    assert "last approval action: requested" in rendered
    assert "please review" in rendered


def test_list_reflects_approval_state(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    result = run_task_list(str(initialized_repo), write_log=False)
    assert result.data["tasks"][0]["approval_state"] == "pending"
    rendered = render_human(result)
    assert "approval=pending" in rendered


def test_validate_reflects_no_approval_issues(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    result = run_task_validate(task_id, str(initialized_repo), write_log=False)
    approval_keys = {b["key"] for b in result.data["blockers"] if "approval" in b["key"]}
    assert approval_keys == set()


# --- security / governance ---------------------------------------------------------


def test_trendforge_target_is_rejected(tmp_path, monkeypatch):
    fake_reference = tmp_path / "FakeTrendForge"
    fake_reference.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(fake_reference), check=True)
    monkeypatch.setattr("forgeops.core.paths.READONLY_REFERENCE_REPO", fake_reference)
    result = run_task_request_approval("task-0001", str(fake_reference), write_log=False, actor="joshua")
    assert result.exit_code == exit_codes.BLOCKED


def test_uninitialized_project_rejected(git_repo):
    result = run_task_request_approval("task-0001", str(git_repo), write_log=False, actor="joshua")
    assert result.exit_code == exit_codes.BLOCKED


def test_no_remote_configured_after_approval_actions(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    run_task_approve(task_id, str(initialized_repo), write_log=False, actor="joshua", confirm=True)
    remotes = subprocess.run(["git", "remote", "-v"], cwd=str(initialized_repo), capture_output=True, text=True).stdout
    assert remotes == ""


# --- regression -----------------------------------------------------------------


def test_regression_task_create_still_works(initialized_repo):
    result = run_task_create("Another Task", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


def test_regression_task_close_still_works(initialized_repo, tmp_path):
    task_id = _create_task(initialized_repo)
    _fill_spec_and_close(initialized_repo, task_id, tmp_path)
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.status == "completed"
