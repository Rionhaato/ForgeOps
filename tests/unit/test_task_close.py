"""Tests for forgeops/state/task_close.py: the read-only preflight plan
builder (reusing task_validate) and the single mutating apply step
behind `forgeops task close`."""
from __future__ import annotations

from forgeops.state.task_close import apply_task_close, build_task_close_plan
from forgeops.state.task_create import apply_task_create, build_task_create_plan
from forgeops.state.task_registry import (
    VALIDATION_STATUS_FAILED,
    VALIDATION_STATUS_PASSED,
    VALIDATION_STATUS_WAIVED,
    load_index,
    load_task_record,
    load_validation_record,
    save_task_record,
    save_validation_record,
    task_dir_for,
)

MAX_BYTES = 2 * 1024 * 1024


def _resolve_ok(repo_root, ref):
    return "deadbeef" if ref else None


def _create_full(repo_root, title="My Task"):
    plan = build_task_create_plan(repo_root, title, None, None, MAX_BYTES)
    outcome = apply_task_create(repo_root, plan)
    assert outcome.ok is True
    task_dir = task_dir_for(repo_root, plan.task_id)
    (task_dir / "SPEC.md").write_text(
        "# SPEC\n\n## Objective\n\nfoo\n\n## In Scope\n\nfoo\n\n## Out of Scope\n\nfoo\n\n"
        "## Constraints\n\nfoo\n\n## Acceptance Criteria\n\n- works\n\n"
        "## Required Validation\n\n- pytest\n\n## Stop Boundary\n\nfoo\n",
        encoding="utf-8",
    )
    return plan.task_id


def _set_validation_status(repo_root, task_id, status, approval_reference=None):
    task_dir = task_dir_for(repo_root, task_id)
    load = load_validation_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "status": status, "approval_reference": approval_reference})
    save_validation_record(task_dir, tampered)


def _plan(repo_root, task_id, result_file=None):
    return build_task_close_plan(repo_root, task_id, result_file, MAX_BYTES, True, _resolve_ok)


# --- preflight -----------------------------------------------------------


def test_missing_result_file_is_a_conflict(initialized_repo):
    task_id = _create_full(initialized_repo)
    _set_validation_status(initialized_repo, task_id, VALIDATION_STATUS_PASSED)
    plan = _plan(initialized_repo, task_id, None)
    assert any(c.key == "missing-result-file" for c in plan.conflicts)


def test_result_file_nonexistent_is_a_conflict(initialized_repo, tmp_path):
    task_id = _create_full(initialized_repo)
    _set_validation_status(initialized_repo, task_id, VALIDATION_STATUS_PASSED)
    plan = _plan(initialized_repo, task_id, tmp_path / "nope.md")
    assert any(c.key == "result-file-invalid" for c in plan.conflicts)


def test_result_file_secret_content_is_a_conflict(initialized_repo, tmp_path):
    task_id = _create_full(initialized_repo)
    _set_validation_status(initialized_repo, task_id, VALIDATION_STATUS_PASSED)
    f = tmp_path / "result.md"
    f.write_text("key: AKIAABCDEFGHIJKLMNOP", encoding="utf-8")
    plan = _plan(initialized_repo, task_id, f)
    assert any(c.key == "result-file-secret-detected" for c in plan.conflicts)


def test_validation_not_run_is_a_conflict(initialized_repo, tmp_path):
    task_id = _create_full(initialized_repo)
    f = tmp_path / "result.md"
    f.write_text("done", encoding="utf-8")
    plan = _plan(initialized_repo, task_id, f)
    assert any(c.key == "validation-not-decided" for c in plan.conflicts)


def test_waived_without_approval_reference_is_a_conflict(initialized_repo, tmp_path):
    task_id = _create_full(initialized_repo)
    _set_validation_status(initialized_repo, task_id, VALIDATION_STATUS_WAIVED, approval_reference=None)
    f = tmp_path / "result.md"
    f.write_text("done", encoding="utf-8")
    plan = _plan(initialized_repo, task_id, f)
    assert any(c.key == "waived-without-approval-reference" for c in plan.conflicts)


def test_waived_with_approval_reference_is_eligible(initialized_repo, tmp_path):
    task_id = _create_full(initialized_repo)
    _set_validation_status(initialized_repo, task_id, VALIDATION_STATUS_WAIVED, approval_reference="approved by joshua 2026-07-23")
    f = tmp_path / "result.md"
    f.write_text("done", encoding="utf-8")
    plan = _plan(initialized_repo, task_id, f)
    assert plan.has_conflict is False
    assert plan.planned_status == "completed"


def test_malformed_validation_is_a_conflict(initialized_repo, tmp_path):
    task_id = _create_full(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "VALIDATION.json").write_text("{ not valid", encoding="utf-8")
    f = tmp_path / "result.md"
    f.write_text("done", encoding="utf-8")
    plan = _plan(initialized_repo, task_id, f)
    assert any(c.key == "validation-json-malformed" for c in plan.conflicts)


def test_already_closed_task_is_a_conflict(initialized_repo, tmp_path):
    task_id = _create_full(initialized_repo)
    _set_validation_status(initialized_repo, task_id, VALIDATION_STATUS_PASSED)
    f = tmp_path / "result.md"
    f.write_text("done", encoding="utf-8")
    plan = _plan(initialized_repo, task_id, f)
    apply_task_close(initialized_repo, plan)
    # second attempt against the now-completed task
    plan2 = _plan(initialized_repo, task_id, f)
    assert any(c.key == "already-closed" for c in plan2.conflicts)


def test_unknown_task_is_a_conflict(initialized_repo, tmp_path):
    f = tmp_path / "result.md"
    f.write_text("done", encoding="utf-8")
    plan = _plan(initialized_repo, "task-9999", f)
    assert plan.has_conflict is True


def test_preflight_never_mutates(initialized_repo, tmp_path):
    task_id = _create_full(initialized_repo)
    _set_validation_status(initialized_repo, task_id, VALIDATION_STATUS_PASSED)
    f = tmp_path / "result.md"
    f.write_text("done", encoding="utf-8")
    before = set(initialized_repo.rglob("*"))
    _plan(initialized_repo, task_id, f)
    after = set(initialized_repo.rglob("*"))
    assert before == after


# --- apply: success -----------------------------------------------------------


def test_passed_validation_closes_completed(initialized_repo, tmp_path):
    task_id = _create_full(initialized_repo)
    _set_validation_status(initialized_repo, task_id, VALIDATION_STATUS_PASSED)
    f = tmp_path / "result.md"
    f.write_text("## Outcome\n\nDone.\n", encoding="utf-8")
    plan = _plan(initialized_repo, task_id, f)
    outcome = apply_task_close(initialized_repo, plan)
    assert outcome.ok is True
    assert outcome.new_status == "completed"

    task_dir = task_dir_for(initialized_repo, task_id)
    assert (task_dir / "RESULT.md").read_text(encoding="utf-8") == "## Outcome\n\nDone.\n"
    record = load_task_record(task_dir).record
    assert record.status == "completed"
    assert record.result_state == "recorded"
    assert record.validation_state == "passed"

    index = load_index(initialized_repo)
    idx_record = next(r for r in index.records if r.task_id == task_id)
    assert idx_record.status == "completed"
    assert idx_record.result_state == "recorded"


def test_failed_validation_closes_failed(initialized_repo, tmp_path):
    task_id = _create_full(initialized_repo)
    _set_validation_status(initialized_repo, task_id, VALIDATION_STATUS_FAILED)
    f = tmp_path / "result.md"
    f.write_text("## Outcome\n\nFailed.\n", encoding="utf-8")
    plan = _plan(initialized_repo, task_id, f)
    outcome = apply_task_close(initialized_repo, plan)
    assert outcome.ok is True
    assert outcome.new_status == "failed"
    record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert record.status == "failed"


def test_apply_never_deletes_task_directory(initialized_repo, tmp_path):
    task_id = _create_full(initialized_repo)
    _set_validation_status(initialized_repo, task_id, VALIDATION_STATUS_PASSED)
    f = tmp_path / "result.md"
    f.write_text("done", encoding="utf-8")
    plan = _plan(initialized_repo, task_id, f)
    apply_task_close(initialized_repo, plan)
    assert task_dir_for(initialized_repo, task_id).is_dir()
    assert (task_dir_for(initialized_repo, task_id) / "SPEC.md").is_file()


# --- apply: TOCTOU / failure handling -------------------------------------------


def test_apply_result_write_failure_reports_partial_state(initialized_repo, tmp_path, monkeypatch):
    task_id = _create_full(initialized_repo)
    _set_validation_status(initialized_repo, task_id, VALIDATION_STATUS_PASSED)
    f = tmp_path / "result.md"
    f.write_text("done", encoding="utf-8")
    plan = _plan(initialized_repo, task_id, f)

    monkeypatch.setattr(
        "forgeops.state.task_close.atomic_write_text",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_close(initialized_repo, plan)
    assert outcome.ok is False
    assert outcome.partial_state is not None
    record = load_task_record(task_dir_for(initialized_repo, task_id)).record
    assert record.status == "draft"  # never touched


def test_apply_task_json_write_failure_rolls_back_result(initialized_repo, tmp_path, monkeypatch):
    task_id = _create_full(initialized_repo)
    _set_validation_status(initialized_repo, task_id, VALIDATION_STATUS_PASSED)
    f = tmp_path / "result.md"
    f.write_text("done", encoding="utf-8")
    plan = _plan(initialized_repo, task_id, f)

    monkeypatch.setattr(
        "forgeops.state.task_close.save_task_record",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_close(initialized_repo, plan)
    assert outcome.ok is False
    assert outcome.partial_state is not None
    task_dir = task_dir_for(initialized_repo, task_id)
    assert not (task_dir / "RESULT.md").exists()  # rolled back
    record = load_task_record(task_dir).record
    assert record.status == "draft"


def test_apply_index_write_failure_still_reports_ok(initialized_repo, tmp_path, monkeypatch):
    task_id = _create_full(initialized_repo)
    _set_validation_status(initialized_repo, task_id, VALIDATION_STATUS_PASSED)
    f = tmp_path / "result.md"
    f.write_text("done", encoding="utf-8")
    plan = _plan(initialized_repo, task_id, f)

    monkeypatch.setattr(
        "forgeops.state.task_close.save_index",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_close(initialized_repo, plan)
    assert outcome.ok is True
    assert outcome.index_written is False
    assert outcome.index_error is not None
    task_dir = task_dir_for(initialized_repo, task_id)
    assert (task_dir / "RESULT.md").is_file()
    record = load_task_record(task_dir).record
    assert record.status == "completed"
