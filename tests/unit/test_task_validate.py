"""Tests for forgeops/state/task_validate.py: read-only structural/
consistency validation for a single task."""
from __future__ import annotations

import json

from forgeops.state.task_create import apply_task_create, build_task_create_plan
from forgeops.state.task_registry import (
    load_index,
    load_task_record,
    load_validation_record,
    save_index,
    save_task_record,
    save_validation_record,
    task_dir_for,
)
from forgeops.state.task_validate import validate_task

MAX_BYTES = 2 * 1024 * 1024


def _resolve_ok(repo_root, ref):
    return "deadbeef" if ref else None


def _resolve_none(repo_root, ref):
    return None


def _create(repo_root, title="My Task", spec_file=None, acceptance_file=None):
    plan = build_task_create_plan(repo_root, title, spec_file, acceptance_file, MAX_BYTES)
    outcome = apply_task_create(repo_root, plan)
    assert outcome.ok is True
    return plan.task_id


def _full_spec(repo_root, task_id):
    """Fill in every required SPEC.md section with real content, so
    validate_task reports zero blockers for an otherwise-untouched task."""
    task_dir = task_dir_for(repo_root, task_id)
    (task_dir / "SPEC.md").write_text(
        "# SPEC\n\n"
        "## Objective\n\nDo the thing.\n\n"
        "## In Scope\n\nThe thing.\n\n"
        "## Out of Scope\n\nEverything else.\n\n"
        "## Constraints\n\nSee CLAUDE.md.\n\n"
        "## Acceptance Criteria\n\n- it works\n\n"
        "## Required Validation\n\n- pytest\n\n"
        "## Stop Boundary\n\nDo not do more.\n",
        encoding="utf-8",
    )


# --- valid task -----------------------------------------------------------


def test_valid_draft_task_has_only_expected_blockers(initialized_repo):
    task_id = _create(initialized_repo)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    # A freshly created draft task has empty Acceptance Criteria/Required
    # Validation placeholders - those are the only expected blockers.
    blocker_keys = {i.key for i in outcome.blockers}
    assert blocker_keys == {"acceptance-criteria-missing", "required-validation-missing"}


def test_fully_specified_task_has_no_blockers(initialized_repo):
    task_id = _create(initialized_repo)
    _full_spec(initialized_repo, task_id)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert outcome.has_blockers is False


def test_validate_never_mutates(initialized_repo):
    task_id = _create(initialized_repo)
    _full_spec(initialized_repo, task_id)
    before = set(initialized_repo.rglob("*"))
    validate_task(initialized_repo, task_id, True, _resolve_ok)
    after = set(initialized_repo.rglob("*"))
    assert before == after


# --- structural issues ------------------------------------------------------


def test_missing_required_heading_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "SPEC.md").write_text("# SPEC\n\n## Objective\n\nfoo\n", encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "spec-missing-headings" for i in outcome.blockers)


def test_invalid_task_id_is_a_blocker(initialized_repo):
    outcome = validate_task(initialized_repo, "not-a-task-id", True, _resolve_ok)
    assert outcome.found is False
    assert any(i.key == "invalid-task-id" for i in outcome.blockers)


def test_unknown_task_id_reports_not_found(initialized_repo):
    outcome = validate_task(initialized_repo, "task-9999", True, _resolve_ok)
    assert outcome.found is False
    assert any(i.key == "not-found" for i in outcome.blockers)


def test_index_directory_mismatch_directory_missing(initialized_repo):
    task_id = _create(initialized_repo)
    import shutil
    shutil.rmtree(task_dir_for(initialized_repo, task_id))
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "index-directory-mismatch" for i in outcome.blockers)


def test_unindexed_task_directory_is_a_warning(initialized_repo):
    task_id = _create(initialized_repo)
    index = load_index(initialized_repo)
    save_index(initialized_repo, type(index)(schema_version=index.schema_version, next_task_number=index.next_task_number, records=[]))
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "index-directory-mismatch" and i.severity == "warning" for i in outcome.issues)


def test_project_root_mismatch_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    load = load_task_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "project_root": "C:\\some\\other\\repo"})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "project-root-mismatch" for i in outcome.blockers)


def test_invalid_status_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    load = load_task_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "status": "bogus_status"})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "invalid-status" for i in outcome.blockers)


def test_invalid_approval_state_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    load = load_task_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "approval_state": "bogus"})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "invalid-approval-state" for i in outcome.blockers)


def test_duplicate_task_id_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    index = load_index(initialized_repo)
    from forgeops.state.task_registry import TaskIndexDocument
    save_index(initialized_repo, TaskIndexDocument(
        schema_version=index.schema_version, next_task_number=index.next_task_number,
        records=[*index.records, index.records[0]],
    ))
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "duplicate-task-id" for i in outcome.blockers)


def test_malformed_validation_json_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "VALIDATION.json").write_text("{ not valid", encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "validation-json-malformed" for i in outcome.blockers)


def test_invalid_validation_status_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    load = load_validation_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "status": "bogus"})
    save_validation_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "invalid-validation-status" for i in outcome.blockers)


def test_result_present_for_non_terminal_status_is_a_warning(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "RESULT.md").write_text("## Outcome\n\nfoo\n", encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "result-present-for-non-terminal-status" and i.severity == "warning" for i in outcome.issues)


def test_result_missing_for_terminal_status_is_a_warning(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    load = load_task_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "status": "completed"})
    save_task_record(task_dir, tampered)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "result-missing-for-terminal-status" and i.severity == "warning" for i in outcome.issues)


def test_path_escape_is_a_blocker(initialized_repo, monkeypatch):
    monkeypatch.setattr("forgeops.state.task_validate.task_dir_for", lambda repo_root, task_id: initialized_repo.parent / "escaped")
    outcome = validate_task(initialized_repo, "task-0001", True, _resolve_ok)
    assert any(i.key == "path-escape" for i in outcome.blockers)


def test_secret_like_content_in_spec_is_a_blocker(initialized_repo):
    task_id = _create(initialized_repo)
    task_dir = task_dir_for(initialized_repo, task_id)
    text = (task_dir / "SPEC.md").read_text(encoding="utf-8")
    (task_dir / "SPEC.md").write_text(text + "\nsecret: AKIAABCDEFGHIJKLMNOP\n", encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert any(i.key == "spec-secret-content" for i in outcome.blockers)


def test_accepted_checkpoint_unresolvable_is_a_warning_when_git_available(initialized_repo):
    task_id = _create(initialized_repo)
    outcome = validate_task(initialized_repo, task_id, True, _resolve_none)
    assert any(i.key == "accepted-checkpoint-unresolvable" and i.severity == "warning" for i in outcome.issues)


def test_accepted_checkpoint_skipped_when_git_unavailable(initialized_repo):
    task_id = _create(initialized_repo)
    outcome = validate_task(initialized_repo, task_id, False, _resolve_none)
    assert not any(i.key == "accepted-checkpoint-unresolvable" for i in outcome.issues)


def test_warnings_and_blockers_are_separated(initialized_repo):
    task_id = _create(initialized_repo)
    _full_spec(initialized_repo, task_id)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "RESULT.md").write_text("## Outcome\n\nfoo\n", encoding="utf-8")
    outcome = validate_task(initialized_repo, task_id, True, _resolve_ok)
    assert outcome.has_blockers is False
    assert len(outcome.warnings) >= 1
