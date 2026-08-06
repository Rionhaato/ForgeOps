"""Tests for forgeops/state/task_approval.py: preflight-plan builders and
apply steps behind `forgeops task request-approval|approve|reject|
cancel-approval`. See docs/approvals.md for the state machine this
exercises directly against `APPROVAL_TRANSITIONS`."""
from __future__ import annotations

import json

from forgeops.state.task_approval import (
    apply_task_approve,
    apply_task_cancel_approval,
    apply_task_reject,
    apply_task_request_approval,
    build_task_approve_plan,
    build_task_cancel_approval_plan,
    build_task_reject_plan,
    build_task_request_approval_plan,
)
from forgeops.state.task_create import apply_task_create, build_task_create_plan
from forgeops.state.task_registry import (
    APPROVAL_STATE_APPROVED,
    APPROVAL_STATE_NOT_REQUESTED,
    APPROVAL_STATE_PENDING,
    APPROVAL_STATE_REJECTED,
    load_index,
    load_task_record,
    save_task_record,
    task_dir_for,
)

MAX_BYTES = 2 * 1024 * 1024


def _create_task(repo_root, title="My Task"):
    plan = build_task_create_plan(repo_root, title, None, None, MAX_BYTES)
    outcome = apply_task_create(repo_root, plan)
    assert outcome.ok is True
    return plan.task_id


# --- request-approval: preflight --------------------------------------------------


def test_request_approval_clean_plan_has_no_conflicts(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua")
    assert plan.has_conflict is False
    assert plan.current_approval_state == APPROVAL_STATE_NOT_REQUESTED
    assert plan.planned_approval_state == APPROVAL_STATE_PENDING


def test_request_approval_plan_never_writes_anything(initialized_repo):
    task_id = _create_task(initialized_repo)
    before = set(initialized_repo.rglob("*"))
    build_task_request_approval_plan(initialized_repo, task_id, "joshua")
    after = set(initialized_repo.rglob("*"))
    assert before == after


def test_request_approval_missing_task(initialized_repo):
    plan = build_task_request_approval_plan(initialized_repo, "task-9999", "joshua")
    assert any(c.key == "task-not-found" for c in plan.conflicts)


def test_request_approval_malformed_task_json(initialized_repo):
    task_id = _create_task(initialized_repo)
    (task_dir_for(initialized_repo, task_id) / "TASK.json").write_text("{ not valid", encoding="utf-8")
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "task-json-malformed" for c in plan.conflicts)


def test_request_approval_malformed_index(initialized_repo):
    task_id = _create_task(initialized_repo)
    index_path = initialized_repo / ".agent" / "tasks" / "TASK_INDEX.json"
    index_path.write_text("{ not valid", encoding="utf-8")
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "task-index-malformed" for c in plan.conflicts)


def test_request_approval_invalid_actor_empty(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_request_approval_plan(initialized_repo, task_id, "")
    assert any(c.key == "invalid-actor" for c in plan.conflicts)


def test_request_approval_invalid_actor_bad_characters(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_request_approval_plan(initialized_repo, task_id, "../../etc/passwd")
    assert any(c.key == "invalid-actor" for c in plan.conflicts)


def test_request_approval_invalid_actor_too_long(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_request_approval_plan(initialized_repo, task_id, "a" * 200)
    assert any(c.key == "invalid-actor" for c in plan.conflicts)


def test_request_approval_secret_like_actor_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_request_approval_plan(initialized_repo, task_id, "AKIAABCDEFGHIJKLMNOP")  # forgeops:allow-secret
    assert any(c.key == "actor-secret-detected" for c in plan.conflicts)


def test_request_approval_oversized_reason_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua", "x" * 3000)
    assert any(c.key == "invalid-reason" for c in plan.conflicts)


def test_request_approval_secret_like_reason_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua", "key is AKIAABCDEFGHIJKLMNOP")  # forgeops:allow-secret
    assert any(c.key == "reason-secret-detected" for c in plan.conflicts)


def test_request_approval_actor_carrying_an_allow_secret_marker_is_still_refused(initialized_repo):
    """The inline allowlist marker annotates *source fixtures* for the
    repository scanner; it is not a runtime escape hatch. An actor that
    embeds the marker is refused before the scanner is even consulted -
    `validate_actor`'s character allow-list is stricter than the scanner
    and rejects the marker's `:`/`#`/whitespace outright."""
    task_id = _create_task(initialized_repo)
    smuggled = "AKIA" + "ABCDEFGHIJKLMNOP" + "  # forgeops:allow-secret"
    plan = build_task_request_approval_plan(initialized_repo, task_id, smuggled)
    assert plan.has_conflict
    assert any(c.key == "invalid-actor" for c in plan.conflicts)


def test_request_approval_reason_carrying_an_allow_secret_marker_is_still_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    smuggled = "key is AKIA" + "ABCDEFGHIJKLMNOP" + " # forgeops:allow-secret"
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua", smuggled)
    assert any(c.key == "reason-secret-detected" for c in plan.conflicts)


def test_request_approval_terminal_task_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    record = load_task_record(task_dir).record
    save_task_record(task_dir, type(record)(**{**record.__dict__, "status": "completed"}))
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "task-already-terminal" for c in plan.conflicts)


def test_request_approval_already_pending_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    apply_task_request_approval(initialized_repo, build_task_request_approval_plan(initialized_repo, task_id, "joshua"))
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "invalid-approval-transition" for c in plan.conflicts)


def test_request_approval_already_approved_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    apply_task_request_approval(initialized_repo, build_task_request_approval_plan(initialized_repo, task_id, "joshua"))
    apply_task_approve(initialized_repo, build_task_approve_plan(initialized_repo, task_id, "joshua"))
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "invalid-approval-transition" for c in plan.conflicts)


def test_request_approval_index_state_mismatch_detected(initialized_repo):
    task_id = _create_task(initialized_repo)
    index_path = initialized_repo / ".agent" / "tasks" / "TASK_INDEX.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["records"][0]["approval_state"] = "pending"
    index_path.write_text(json.dumps(payload), encoding="utf-8")
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "task-index-approval-mismatch" for c in plan.conflicts)


# --- request-approval: apply / success ----------------------------------------------


def test_request_approval_success_from_not_requested(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua", "please review")
    outcome = apply_task_request_approval(initialized_repo, plan)
    assert outcome.ok is True
    assert outcome.task_json_written is True
    assert outcome.index_written is True
    assert outcome.new_approval_state == APPROVAL_STATE_PENDING

    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.approval_state == APPROVAL_STATE_PENDING
    assert len(task_record.approval_history) == 1
    event = task_record.approval_history[0]
    assert event.action == "requested"
    assert event.actor == "joshua"
    assert event.reason == "please review"
    assert event.timestamp

    index = load_index(initialized_repo)
    idx_record = next(r for r in index.records if r.task_id == task_id)
    assert idx_record.approval_state == APPROVAL_STATE_PENDING


def test_request_approval_success_from_rejected(initialized_repo):
    task_id = _create_task(initialized_repo)
    apply_task_request_approval(initialized_repo, build_task_request_approval_plan(initialized_repo, task_id, "joshua"))
    apply_task_reject(initialized_repo, build_task_reject_plan(initialized_repo, task_id, "joshua", "not ready"))
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua")
    outcome = apply_task_request_approval(initialized_repo, plan)
    assert outcome.ok is True
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.approval_state == APPROVAL_STATE_PENDING
    assert len(task_record.approval_history) == 3


def test_request_approval_does_not_touch_task_status_or_ownership(initialized_repo):
    task_id = _create_task(initialized_repo)
    apply_task_request_approval(initialized_repo, build_task_request_approval_plan(initialized_repo, task_id, "joshua"))
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.status == "draft"
    assert task_record.worktree_id is None
    assert task_record.agent_id is None


def test_request_approval_toctou_state_changed(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua")
    assert not plan.has_conflict
    apply_task_request_approval(initialized_repo, build_task_request_approval_plan(initialized_repo, task_id, "joshua"))
    outcome = apply_task_request_approval(initialized_repo, plan)
    assert outcome.ok is False
    assert outcome.partial_state is not None


def test_request_approval_task_json_write_failure(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua")
    monkeypatch.setattr(
        "forgeops.state.task_approval.save_task_record",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_request_approval(initialized_repo, plan)
    assert outcome.ok is False
    assert outcome.task_json_written is False


def test_request_approval_index_write_failure_rolls_back(initialized_repo, monkeypatch):
    task_id = _create_task(initialized_repo)
    plan = build_task_request_approval_plan(initialized_repo, task_id, "joshua")
    monkeypatch.setattr(
        "forgeops.state.task_approval.save_task_index",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_request_approval(initialized_repo, plan)
    assert outcome.ok is False
    assert outcome.partial_state.get("rolled_back") is True
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.approval_state == APPROVAL_STATE_NOT_REQUESTED
    assert task_record.approval_history == []


# --- approve --------------------------------------------------------------------


def _request(repo_root, task_id, actor="joshua"):
    apply_task_request_approval(repo_root, build_task_request_approval_plan(repo_root, task_id, actor))


def test_approve_clean_plan_from_pending(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    plan = build_task_approve_plan(initialized_repo, task_id, "joshua")
    assert plan.has_conflict is False
    assert plan.planned_approval_state == APPROVAL_STATE_APPROVED


def test_approve_from_not_requested_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_approve_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "invalid-approval-transition" for c in plan.conflicts)


def test_approve_from_rejected_without_new_request_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    apply_task_reject(initialized_repo, build_task_reject_plan(initialized_repo, task_id, "joshua", "no"))
    plan = build_task_approve_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "invalid-approval-transition" for c in plan.conflicts)


def test_approve_already_approved_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    apply_task_approve(initialized_repo, build_task_approve_plan(initialized_repo, task_id, "joshua"))
    plan = build_task_approve_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "invalid-approval-transition" for c in plan.conflicts)


def test_approve_success_records_history_and_state(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    outcome = apply_task_approve(initialized_repo, build_task_approve_plan(initialized_repo, task_id, "joshua"))
    assert outcome.ok is True
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.approval_state == APPROVAL_STATE_APPROVED
    assert [e.action for e in task_record.approval_history] == ["requested", "approved"]


def test_approve_reason_optional(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    outcome = apply_task_approve(initialized_repo, build_task_approve_plan(initialized_repo, task_id, "joshua"))
    assert outcome.ok is True


# --- reject ---------------------------------------------------------------------


def test_reject_requires_reason(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    plan = build_task_reject_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "reason-required" for c in plan.conflicts)


def test_reject_with_whitespace_only_reason_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    plan = build_task_reject_plan(initialized_repo, task_id, "joshua", "   ")
    assert any(c.key == "reason-required" for c in plan.conflicts)


def test_reject_from_not_requested_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_reject_plan(initialized_repo, task_id, "joshua", "no")
    assert any(c.key == "invalid-approval-transition" for c in plan.conflicts)


def test_reject_success(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    outcome = apply_task_reject(initialized_repo, build_task_reject_plan(initialized_repo, task_id, "joshua", "not ready"))
    assert outcome.ok is True
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.approval_state == APPROVAL_STATE_REJECTED
    assert task_record.approval_history[-1].reason == "not ready"


# --- cancel-approval --------------------------------------------------------------


def test_cancel_approval_clean_plan_from_pending(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    plan = build_task_cancel_approval_plan(initialized_repo, task_id, "joshua")
    assert plan.has_conflict is False
    assert plan.planned_approval_state == APPROVAL_STATE_NOT_REQUESTED


def test_cancel_approval_from_not_requested_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    plan = build_task_cancel_approval_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "invalid-approval-transition" for c in plan.conflicts)


def test_cancel_approval_from_approved_refused(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    apply_task_approve(initialized_repo, build_task_approve_plan(initialized_repo, task_id, "joshua"))
    plan = build_task_cancel_approval_plan(initialized_repo, task_id, "joshua")
    assert any(c.key == "invalid-approval-transition" for c in plan.conflicts)


def test_cancel_approval_success(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    outcome = apply_task_cancel_approval(initialized_repo, build_task_cancel_approval_plan(initialized_repo, task_id, "joshua"))
    assert outcome.ok is True
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert task_record.approval_state == APPROVAL_STATE_NOT_REQUESTED
    assert [e.action for e in task_record.approval_history] == ["requested", "cancelled"]


def test_cancel_approval_history_is_append_only_never_shrinks(initialized_repo):
    task_id = _create_task(initialized_repo)
    _request(initialized_repo, task_id)
    apply_task_cancel_approval(initialized_repo, build_task_cancel_approval_plan(initialized_repo, task_id, "joshua"))
    _request(initialized_repo, task_id)
    task_record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert len(task_record.approval_history) == 3
    assert [e.action for e in task_record.approval_history] == ["requested", "cancelled", "requested"]
