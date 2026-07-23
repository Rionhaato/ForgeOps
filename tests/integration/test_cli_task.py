"""Integration tests for `forgeops task create|show|list|validate|close`
(forgeops/cli/task.py). See docs/tasks.md for the directory contract,
lifecycle, and confirmation model this exercises end-to-end through the
CLI entry points."""
from __future__ import annotations

import json
from pathlib import Path

from forgeops.cli.task import (
    render_human,
    run_task_close,
    run_task_create,
    run_task_list,
    run_task_show,
    run_task_validate,
)
from forgeops.core import exit_codes
from forgeops.state.task_registry import load_index, load_validation_record, save_validation_record, task_dir_for


def _create(repo, title="My Task", **kwargs):
    result = run_task_create(title, str(repo), write_log=False, **kwargs)
    assert result.exit_code == exit_codes.SUCCESS
    return result


def _fill_spec(repo, task_id):
    task_dir = task_dir_for(repo, task_id)
    (task_dir / "SPEC.md").write_text(
        "# SPEC\n\n## Objective\n\nfoo\n\n## In Scope\n\nfoo\n\n## Out of Scope\n\nfoo\n\n"
        "## Constraints\n\nfoo\n\n## Acceptance Criteria\n\n- works\n\n"
        "## Required Validation\n\n- pytest\n\n## Stop Boundary\n\nfoo\n",
        encoding="utf-8",
    )


def _set_validation_passed(repo, task_id, approval_reference=None, status="passed"):
    task_dir = task_dir_for(repo, task_id)
    load = load_validation_record(task_dir)
    tampered = type(load.record)(**{**load.record.__dict__, "status": status, "approval_reference": approval_reference})
    save_validation_record(task_dir, tampered)


# --- task create ---------------------------------------------------------------


def test_create_clean(initialized_repo):
    result = _create(initialized_repo, "My Task")
    assert result.data["task_id"] == "task-0001"
    assert task_dir_for(initialized_repo, "task-0001").is_dir()


def test_create_monotonic_ids(initialized_repo):
    r1 = _create(initialized_repo, "Task One")
    r2 = _create(initialized_repo, "Task Two")
    assert r1.data["task_id"] == "task-0001"
    assert r2.data["task_id"] == "task-0002"


def test_create_explicit_repository(initialized_repo):
    result = run_task_create("My Task", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


def test_create_current_directory_repository(initialized_repo):
    result = run_task_create("My Task", None, cwd=initialized_repo, write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


def test_create_spacey_path(spacey_initialized_repo):
    result = _create(spacey_initialized_repo, "My Task")
    assert task_dir_for(spacey_initialized_repo, "task-0001").is_dir()


def test_create_dry_run_performs_no_mutation(initialized_repo):
    before = set(initialized_repo.rglob("*"))
    result = run_task_create("My Task", str(initialized_repo), write_log=False, dry_run=True)
    after = set(initialized_repo.rglob("*"))
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["action"] == "would_create_task"
    assert before == after


def test_create_human_json_agree(initialized_repo):
    result = _create(initialized_repo, "My Task")
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops task create" in rendered
    assert payload["data"]["task_id"] == "task-0001"
    assert payload["exit_code"] == result.exit_code


def test_create_default_minimal_spec_template(initialized_repo):
    result = _create(initialized_repo, "My Task")
    spec = (task_dir_for(initialized_repo, result.data["task_id"]) / "SPEC.md").read_text(encoding="utf-8")
    assert "not yet defined" in spec
    assert "## Objective" in spec


def test_create_supplied_spec_file(initialized_repo, tmp_path):
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("# Custom Spec\n\nfreeform\n", encoding="utf-8")
    result = _create(initialized_repo, "My Task", spec_file=str(spec_file))
    content = (task_dir_for(initialized_repo, result.data["task_id"]) / "SPEC.md").read_text(encoding="utf-8")
    assert content == "# Custom Spec\n\nfreeform\n"


def test_create_supplied_acceptance_file(initialized_repo, tmp_path):
    acc_file = tmp_path / "acceptance.txt"
    acc_file.write_text("- one\n- two\n", encoding="utf-8")
    result = _create(initialized_repo, "My Task", acceptance_file=str(acc_file))
    content = (task_dir_for(initialized_repo, result.data["task_id"]) / "SPEC.md").read_text(encoding="utf-8")
    assert "- one" in content
    assert "- two" in content


def test_create_nonexistent_source_file(initialized_repo, tmp_path):
    result = run_task_create("My Task", str(initialized_repo), write_log=False, spec_file=str(tmp_path / "nope.md"))
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "spec-file-invalid" for c in result.data["conflicts"])


def test_create_oversized_source_file(initialized_repo, tmp_path, monkeypatch):
    f = tmp_path / "spec.md"
    f.write_text("x" * 1000, encoding="utf-8")
    monkeypatch.setattr("forgeops.cli.task.load_config", lambda repo_root: {"secret_scan_max_file_bytes": 10})
    result = run_task_create("My Task", str(initialized_repo), write_log=False, spec_file=str(f))
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "spec-file-invalid" for c in result.data["conflicts"])


def test_create_secret_content_rejected(initialized_repo, tmp_path):
    f = tmp_path / "spec.md"
    f.write_text("key: AKIAABCDEFGHIJKLMNOP", encoding="utf-8")
    result = run_task_create("My Task", str(initialized_repo), write_log=False, spec_file=str(f))
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "spec-file-secret-detected" for c in result.data["conflicts"])


def test_create_uninitialized_project_rejected(git_repo):
    result = run_task_create("My Task", str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c.id == "project-initialized" for c in result.checks)


def test_create_protected_target_rejected(initialized_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.task.is_protected_reference_path", lambda p: True)
    result = run_task_create("My Task", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c.id == "reference-repo-protection" for c in result.checks)


def test_create_path_containment(initialized_repo):
    result = _create(initialized_repo, "My Task")
    task_path = Path(result.data["task_path"])
    assert not task_path.is_absolute()
    resolved = (initialized_repo / task_path).resolve()
    managed_root = (initialized_repo / ".agent" / "tasks").resolve()
    assert resolved.is_relative_to(managed_root)


def test_create_atomic_index_and_task(initialized_repo):
    _create(initialized_repo, "My Task")
    index = load_index(initialized_repo)
    assert len(index.records) == 1
    assert index.next_task_number == 2


def test_create_simulated_write_failure(initialized_repo, monkeypatch):
    monkeypatch.setattr(
        "forgeops.cli.task.apply_task_create",
        lambda repo_root, plan, clock=None: __import__(
            "forgeops.state.task_create", fromlist=["TaskCreateOutcome"]
        ).TaskCreateOutcome(
            ok=False, task_id="task-0001", task_dir_created=True, task_json_written=False,
            spec_written=False, validation_written=False, index_written=False, index_error=None,
            partial_state={"error": "simulated failure"},
        ),
    )
    result = run_task_create("My Task", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    assert result.data["partial_state"] is not None
    assert "manual_recovery_recommendation" in result.data


def test_create_malformed_index_fails_closed(initialized_repo):
    index_path = initialized_repo / ".agent" / "tasks" / "TASK_INDEX.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text("{ not valid", encoding="utf-8")
    result = run_task_create("My Task", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "index-malformed" for c in result.data["conflicts"])
    assert index_path.read_text(encoding="utf-8") == "{ not valid"


def test_create_no_git_executable_returns_command_execution_failure(initialized_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.task.git_version", lambda: None)
    result = run_task_create("My Task", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE


# --- task show / list --------------------------------------------------------------


def test_show_existing_task(initialized_repo):
    created = _create(initialized_repo, "My Task")
    result = run_task_show(created.data["task_id"], str(initialized_repo), write_log=False)
    assert result.data["found"] is True
    assert result.data["task"]["title"] == "My Task"


def test_show_unknown_task(initialized_repo):
    result = run_task_show("task-9999", str(initialized_repo), write_log=False)
    assert result.data["found"] is False
    assert result.exit_code == exit_codes.BLOCKED


def test_show_secret_safe_output(initialized_repo):
    created = _create(initialized_repo, "My Task")
    result = run_task_show(created.data["task_id"], str(initialized_repo), write_log=False)
    assert "AKIA" not in result.to_json()


def test_show_human_json_agree(initialized_repo):
    created = _create(initialized_repo, "My Task")
    result = run_task_show(created.data["task_id"], str(initialized_repo), write_log=False)
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops task show" in rendered
    assert payload["data"]["task"]["task_id"] == created.data["task_id"]


def test_list_empty(initialized_repo):
    result = run_task_list(str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["tasks"] == []


def test_list_multiple(initialized_repo):
    _create(initialized_repo, "Task One")
    _create(initialized_repo, "Task Two")
    result = run_task_list(str(initialized_repo), write_log=False)
    assert result.data["total"] == 2


def test_list_status_filtering(initialized_repo):
    _create(initialized_repo, "Task One")
    result = run_task_list(str(initialized_repo), write_log=False, status_filter="draft")
    assert result.data["total"] == 1
    result2 = run_task_list(str(initialized_repo), write_log=False, status_filter="completed")
    assert result2.data["total"] == 0


def test_list_malformed_task_artifact_reported_via_show(initialized_repo):
    created = _create(initialized_repo, "My Task")
    task_dir = task_dir_for(initialized_repo, created.data["task_id"])
    (task_dir / "TASK.json").write_text("{ not valid", encoding="utf-8")
    result = run_task_show(created.data["task_id"], str(initialized_repo), write_log=False)
    assert any(i["key"] == "task-json-malformed" for i in result.data["issues"])


def test_list_stale_index_entry(initialized_repo):
    import shutil
    created = _create(initialized_repo, "My Task")
    shutil.rmtree(task_dir_for(initialized_repo, created.data["task_id"]))
    result = run_task_list(str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    assert created.data["task_id"] in result.data["stale_index_entries"]


def test_list_unindexed_task_directory(initialized_repo):
    created = _create(initialized_repo, "Second Task")
    index_path = initialized_repo / ".agent" / "tasks" / "TASK_INDEX.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["records"] = []
    index_path.write_text(json.dumps(payload), encoding="utf-8")
    result = run_task_list(str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    assert created.data["task_id"] in result.data["unindexed_directories"]


def test_list_concise_human_output(initialized_repo):
    _create(initialized_repo, "My Task")
    result = run_task_list(str(initialized_repo), write_log=False)
    rendered = render_human(result)
    assert "My Task" in rendered
    assert "[draft]" in rendered


def test_list_json_output(initialized_repo):
    _create(initialized_repo, "My Task")
    result = run_task_list(str(initialized_repo), write_log=False)
    payload = json.loads(result.to_json())
    assert payload["command"] == "task-list"


def test_list_read_only_no_mutation(initialized_repo):
    _create(initialized_repo, "My Task")
    before = set(initialized_repo.rglob("*"))
    run_task_list(str(initialized_repo), write_log=False)
    after = set(initialized_repo.rglob("*"))
    assert before == after


# --- task validate ------------------------------------------------------------------


def test_validate_draft_task_reports_expected_blockers(initialized_repo):
    created = _create(initialized_repo, "My Task")
    result = run_task_validate(created.data["task_id"], str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    assert result.data["valid"] is False


def test_validate_fully_specified_task_passes(initialized_repo):
    created = _create(initialized_repo, "My Task")
    _fill_spec(initialized_repo, created.data["task_id"])
    result = run_task_validate(created.data["task_id"], str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["valid"] is True


def test_validate_invalid_task_id(initialized_repo):
    result = run_task_validate("not-a-task-id", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED


def test_validate_no_mutation(initialized_repo):
    created = _create(initialized_repo, "My Task")
    before = set(initialized_repo.rglob("*"))
    run_task_validate(created.data["task_id"], str(initialized_repo), write_log=False)
    after = set(initialized_repo.rglob("*"))
    assert before == after


def test_validate_human_json_agree(initialized_repo):
    created = _create(initialized_repo, "My Task")
    result = run_task_validate(created.data["task_id"], str(initialized_repo), write_log=False)
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops task validate" in rendered
    assert payload["data"]["task_id"] == created.data["task_id"]


# --- task close -----------------------------------------------------------------


def test_close_missing_confirm_no_mutation(initialized_repo, tmp_path):
    created = _create(initialized_repo, "My Task")
    task_id = created.data["task_id"]
    _fill_spec(initialized_repo, task_id)
    _set_validation_passed(initialized_repo, task_id)
    result_file = tmp_path / "result.md"
    result_file.write_text("## Outcome\n\ndone\n", encoding="utf-8")

    before = set(initialized_repo.rglob("*"))
    result = run_task_close(task_id, str(initialized_repo), write_log=False, result_file=str(result_file))
    after = set(initialized_repo.rglob("*"))
    assert result.exit_code == exit_codes.BLOCKED
    assert result.data["action"] == "confirmation_required"
    assert before == after


def test_close_dry_run_no_mutation(initialized_repo, tmp_path):
    created = _create(initialized_repo, "My Task")
    task_id = created.data["task_id"]
    _fill_spec(initialized_repo, task_id)
    _set_validation_passed(initialized_repo, task_id)
    result_file = tmp_path / "result.md"
    result_file.write_text("## Outcome\n\ndone\n", encoding="utf-8")

    before = set(initialized_repo.rglob("*"))
    result = run_task_close(task_id, str(initialized_repo), write_log=False, dry_run=True, result_file=str(result_file))
    after = set(initialized_repo.rglob("*"))
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["action"] == "would_close_task"
    assert result.data["planned_status"] == "completed"
    assert before == after


def test_close_passed_validation_closes_completed(initialized_repo, tmp_path):
    created = _create(initialized_repo, "My Task")
    task_id = created.data["task_id"]
    _fill_spec(initialized_repo, task_id)
    _set_validation_passed(initialized_repo, task_id)
    result_file = tmp_path / "result.md"
    result_file.write_text("## Outcome\n\ndone\n", encoding="utf-8")

    result = run_task_close(task_id, str(initialized_repo), write_log=False, confirm=True, result_file=str(result_file))
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["new_status"] == "completed"
    assert (task_dir_for(initialized_repo, task_id) / "RESULT.md").is_file()


def test_close_failed_validation_closes_failed(initialized_repo, tmp_path):
    created = _create(initialized_repo, "My Task")
    task_id = created.data["task_id"]
    _fill_spec(initialized_repo, task_id)
    _set_validation_passed(initialized_repo, task_id, status="failed")
    result_file = tmp_path / "result.md"
    result_file.write_text("## Outcome\n\nfailed\n", encoding="utf-8")

    result = run_task_close(task_id, str(initialized_repo), write_log=False, confirm=True, result_file=str(result_file))
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["new_status"] == "failed"


def test_close_waived_requires_approval_reference(initialized_repo, tmp_path):
    created = _create(initialized_repo, "My Task")
    task_id = created.data["task_id"]
    _fill_spec(initialized_repo, task_id)
    _set_validation_passed(initialized_repo, task_id, status="waived", approval_reference=None)
    result_file = tmp_path / "result.md"
    result_file.write_text("done", encoding="utf-8")

    result = run_task_close(task_id, str(initialized_repo), write_log=False, confirm=True, result_file=str(result_file))
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "waived-without-approval-reference" for c in result.data["conflicts"])


def test_close_missing_result_file(initialized_repo):
    created = _create(initialized_repo, "My Task")
    task_id = created.data["task_id"]
    _fill_spec(initialized_repo, task_id)
    _set_validation_passed(initialized_repo, task_id)
    result = run_task_close(task_id, str(initialized_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "missing-result-file" for c in result.data["conflicts"])


def test_close_malformed_validation(initialized_repo, tmp_path):
    created = _create(initialized_repo, "My Task")
    task_id = created.data["task_id"]
    _fill_spec(initialized_repo, task_id)
    task_dir = task_dir_for(initialized_repo, task_id)
    (task_dir / "VALIDATION.json").write_text("{ not valid", encoding="utf-8")
    result_file = tmp_path / "result.md"
    result_file.write_text("done", encoding="utf-8")
    result = run_task_close(task_id, str(initialized_repo), write_log=False, confirm=True, result_file=str(result_file))
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "validation-json-malformed" for c in result.data["conflicts"])


def test_close_result_contains_secret_like_content(initialized_repo, tmp_path):
    created = _create(initialized_repo, "My Task")
    task_id = created.data["task_id"]
    _fill_spec(initialized_repo, task_id)
    _set_validation_passed(initialized_repo, task_id)
    result_file = tmp_path / "result.md"
    result_file.write_text("token: AKIAABCDEFGHIJKLMNOP", encoding="utf-8")
    result = run_task_close(task_id, str(initialized_repo), write_log=False, confirm=True, result_file=str(result_file))
    assert result.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "result-file-secret-detected" for c in result.data["conflicts"])


def test_close_already_closed_task(initialized_repo, tmp_path):
    created = _create(initialized_repo, "My Task")
    task_id = created.data["task_id"]
    _fill_spec(initialized_repo, task_id)
    _set_validation_passed(initialized_repo, task_id)
    result_file = tmp_path / "result.md"
    result_file.write_text("done", encoding="utf-8")
    run_task_close(task_id, str(initialized_repo), write_log=False, confirm=True, result_file=str(result_file))

    second = run_task_close(task_id, str(initialized_repo), write_log=False, confirm=True, result_file=str(result_file))
    assert second.exit_code == exit_codes.BLOCKED
    assert any(c["key"] == "already-closed" for c in second.data["conflicts"])


def test_close_atomic_updates(initialized_repo, tmp_path):
    created = _create(initialized_repo, "My Task")
    task_id = created.data["task_id"]
    _fill_spec(initialized_repo, task_id)
    _set_validation_passed(initialized_repo, task_id)
    result_file = tmp_path / "result.md"
    result_file.write_text("## Outcome\n\ndone\n", encoding="utf-8")
    run_task_close(task_id, str(initialized_repo), write_log=False, confirm=True, result_file=str(result_file))

    index = load_index(initialized_repo)
    idx = next(r for r in index.records if r.task_id == task_id)
    show = run_task_show(task_id, str(initialized_repo), write_log=False)
    assert idx.status == "completed"
    assert show.data["task"]["status"] == "completed"
    assert show.data["result_present"] is True


def test_close_simulated_partial_write_failure(initialized_repo, tmp_path, monkeypatch):
    created = _create(initialized_repo, "My Task")
    task_id = created.data["task_id"]
    _fill_spec(initialized_repo, task_id)
    _set_validation_passed(initialized_repo, task_id)
    result_file = tmp_path / "result.md"
    result_file.write_text("done", encoding="utf-8")

    from forgeops.state.task_close import TaskCloseOutcome
    monkeypatch.setattr(
        "forgeops.cli.task.apply_task_close",
        lambda repo_root, plan, clock=None: TaskCloseOutcome(
            ok=False, new_status=None, result_written=False, task_json_written=False,
            index_written=False, index_error=None, partial_state={"error": "simulated failure"},
        ),
    )
    result = run_task_close(task_id, str(initialized_repo), write_log=False, confirm=True, result_file=str(result_file))
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
    assert result.data["partial_state"] is not None
    assert "manual_recovery_recommendation" in result.data


def test_close_human_json_agree(initialized_repo, tmp_path):
    created = _create(initialized_repo, "My Task")
    task_id = created.data["task_id"]
    _fill_spec(initialized_repo, task_id)
    _set_validation_passed(initialized_repo, task_id)
    result_file = tmp_path / "result.md"
    result_file.write_text("done", encoding="utf-8")
    result = run_task_close(task_id, str(initialized_repo), write_log=False, confirm=True, result_file=str(result_file))
    rendered = render_human(result)
    payload = json.loads(result.to_json())
    assert "forgeops task close" in rendered
    assert payload["data"]["new_status"] == "completed"


def test_close_never_deletes_task_directory(initialized_repo, tmp_path):
    created = _create(initialized_repo, "My Task")
    task_id = created.data["task_id"]
    _fill_spec(initialized_repo, task_id)
    _set_validation_passed(initialized_repo, task_id)
    result_file = tmp_path / "result.md"
    result_file.write_text("done", encoding="utf-8")
    run_task_close(task_id, str(initialized_repo), write_log=False, confirm=True, result_file=str(result_file))
    assert task_dir_for(initialized_repo, task_id).is_dir()


def test_close_no_git_executable_returns_command_execution_failure(initialized_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.task.git_version", lambda: None)
    result = run_task_close("task-0001", str(initialized_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE


# --- protected reference repository / uninitialized ---------------------------------


def test_all_commands_reject_uninitialized_project(git_repo):
    assert run_task_show("task-0001", str(git_repo), write_log=False).exit_code == exit_codes.BLOCKED
    assert run_task_list(str(git_repo), write_log=False).exit_code == exit_codes.BLOCKED
    assert run_task_validate("task-0001", str(git_repo), write_log=False).exit_code == exit_codes.BLOCKED
    assert run_task_close("task-0001", str(git_repo), write_log=False, confirm=True).exit_code == exit_codes.BLOCKED


def test_trendforge_target_is_rejected(tmp_path, monkeypatch):
    import subprocess
    fake_reference = tmp_path / "FakeTrendForge"
    fake_reference.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(fake_reference), check=True)
    monkeypatch.setattr("forgeops.core.paths.READONLY_REFERENCE_REPO", fake_reference)
    result = run_task_create("My Task", str(fake_reference), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED


# --- regression: other CLI commands unaffected -------------------------------------


def test_regression_worktree_list(initialized_repo):
    from forgeops.cli.worktree import run_worktree_list
    result = run_worktree_list(str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


def test_regression_worktree_create(initialized_repo):
    from forgeops.cli.worktree import run_worktree_create
    result = run_worktree_create("demo", str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


def test_regression_worktree_remove(initialized_repo):
    from forgeops.cli.worktree import run_worktree_create, run_worktree_remove
    run_worktree_create("demo", str(initialized_repo), write_log=False)
    result = run_worktree_remove("demo", str(initialized_repo), write_log=False, confirm=True)
    assert result.exit_code == exit_codes.SUCCESS


def test_regression_init(tmp_path):
    from forgeops.cli.init import run_init
    result = run_init(str(tmp_path), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


def test_regression_cleanup(initialized_repo):
    from forgeops.cli.cleanup import run_cleanup
    result = run_cleanup(str(initialized_repo), write_log=False)
    assert result.exit_code in (exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT)


def test_regression_process_list(initialized_repo):
    from forgeops.cli.process_list import run_process_list
    result = run_process_list(str(initialized_repo))
    assert result.exit_code in (exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT)


def test_regression_resume_context(initialized_repo):
    from forgeops.cli.resume_context import run_resume_context
    result = run_resume_context(str(initialized_repo))
    assert result.exit_code in (exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT)


def test_regression_checkpoint(initialized_repo):
    from forgeops.cli.checkpoint import run_checkpoint
    result = run_checkpoint(str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


def test_regression_handoff(initialized_repo):
    from forgeops.cli.handoff import run_handoff
    result = run_handoff(str(initialized_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS


def test_regression_release_check(initialized_repo):
    from forgeops.cli.release_check import run_release_check
    result = run_release_check(str(initialized_repo))
    assert result.exit_code in (exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT, exit_codes.BLOCKED, exit_codes.COMMAND_EXECUTION_FAILURE)


def test_regression_test_targeted(initialized_repo):
    from forgeops.cli.test import run_test_targeted
    result = run_test_targeted(str(initialized_repo), plan_only=True)
    assert result.exit_code in (exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT)


def test_regression_test_full(initialized_repo):
    from forgeops.cli.test import run_full_test
    result = run_full_test(str(initialized_repo), plan_only=True)
    assert result.exit_code in (exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT)


def test_regression_doctor(initialized_repo):
    from forgeops.cli.doctor import run_doctor
    result = run_doctor(str(initialized_repo))
    assert result.exit_code in (exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT)


def test_regression_status(initialized_repo):
    from forgeops.cli.status import run_status
    result = run_status(str(initialized_repo))
    assert result.exit_code == exit_codes.SUCCESS


def test_regression_audit(initialized_repo):
    from forgeops.cli.audit import run_audit
    result = run_audit(str(initialized_repo))
    assert result.exit_code in (exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT)


def test_regression_changed(initialized_repo):
    from forgeops.cli.changed import run_changed
    result = run_changed(str(initialized_repo))
    assert result.exit_code in (exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT)
