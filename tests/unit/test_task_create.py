"""Tests for forgeops/state/task_create.py: the read-only preflight plan
builder and the single mutating apply step behind `forgeops task create`."""
from __future__ import annotations

import json

from forgeops.state.task_create import apply_task_create, build_task_create_plan
from forgeops.state.task_registry import load_index, load_task_record, load_validation_record

MAX_BYTES = 2 * 1024 * 1024


# --- preflight: clean plan -----------------------------------------------


def test_clean_plan_has_no_conflicts(initialized_repo):
    plan = build_task_create_plan(initialized_repo, "My Task", None, None, MAX_BYTES)
    assert plan.has_conflict is False
    assert plan.task_id == "task-0001"
    assert plan.source_branch is not None
    assert plan.source_head is not None


def test_build_plan_never_writes_anything(initialized_repo):
    before = set(initialized_repo.rglob("*"))
    build_task_create_plan(initialized_repo, "My Task", None, None, MAX_BYTES)
    after = set(initialized_repo.rglob("*"))
    assert before == after


def test_not_initialized_project_is_a_conflict(git_repo):
    plan = build_task_create_plan(git_repo, "My Task", None, None, MAX_BYTES)
    assert any(c.key == "not-initialized" for c in plan.conflicts)


def test_protected_reference_repo_is_a_conflict(initialized_repo, monkeypatch):
    monkeypatch.setattr("forgeops.state.task_create.is_protected_reference_path", lambda p: True)
    plan = build_task_create_plan(initialized_repo, "My Task", None, None, MAX_BYTES)
    assert any(c.key == "protected-reference-repo" for c in plan.conflicts)


def test_empty_title_is_a_conflict(initialized_repo):
    plan = build_task_create_plan(initialized_repo, "  ", None, None, MAX_BYTES)
    assert any(c.key == "invalid-title" for c in plan.conflicts)


def test_oversized_title_is_a_conflict(initialized_repo):
    plan = build_task_create_plan(initialized_repo, "x" * 500, None, None, MAX_BYTES)
    assert any(c.key == "invalid-title" for c in plan.conflicts)


def test_multiline_title_is_a_conflict(initialized_repo):
    plan = build_task_create_plan(initialized_repo, "line one\nline two", None, None, MAX_BYTES)
    assert any(c.key == "invalid-title" for c in plan.conflicts)


def test_malformed_index_is_a_conflict(initialized_repo):
    index_path = initialized_repo / ".agent" / "tasks" / "TASK_INDEX.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text("{ not valid", encoding="utf-8")
    plan = build_task_create_plan(initialized_repo, "My Task", None, None, MAX_BYTES)
    assert any(c.key == "index-malformed" for c in plan.conflicts)


def test_destination_exists_is_a_conflict(initialized_repo):
    task_dir = initialized_repo / ".agent" / "tasks" / "task-0001"
    task_dir.mkdir(parents=True)
    plan = build_task_create_plan(initialized_repo, "My Task", None, None, MAX_BYTES)
    assert any(c.key == "destination-exists" for c in plan.conflicts)


# --- preflight: spec-file / acceptance-file -----------------------------------


def test_spec_file_nonexistent_is_a_conflict(initialized_repo, tmp_path):
    plan = build_task_create_plan(initialized_repo, "My Task", tmp_path / "nope.md", None, MAX_BYTES)
    assert any(c.key == "spec-file-invalid" for c in plan.conflicts)


def test_spec_file_directory_is_a_conflict(initialized_repo, tmp_path):
    d = tmp_path / "adir"
    d.mkdir()
    plan = build_task_create_plan(initialized_repo, "My Task", d, None, MAX_BYTES)
    assert any(c.key == "spec-file-invalid" for c in plan.conflicts)


def test_spec_file_oversized_is_a_conflict(initialized_repo, tmp_path):
    f = tmp_path / "spec.md"
    f.write_text("x" * 1000, encoding="utf-8")
    plan = build_task_create_plan(initialized_repo, "My Task", f, None, max_source_bytes=10)
    assert any(c.key == "spec-file-invalid" for c in plan.conflicts)


def test_spec_file_secret_content_is_a_conflict(initialized_repo, tmp_path):
    f = tmp_path / "spec.md"
    f.write_text("aws key AKIAABCDEFGHIJKLMNOP here", encoding="utf-8")  # forgeops:allow-secret
    plan = build_task_create_plan(initialized_repo, "My Task", f, None, MAX_BYTES)
    assert any(c.key == "spec-file-secret-detected" for c in plan.conflicts)


def test_spec_file_cannot_self_exempt_with_an_allow_secret_marker(initialized_repo, tmp_path):
    """Ingested content is scanned under a synthetic `.agent/...` path
    that matches no approved zone, so a spec file cannot smuggle a secret
    past preflight by annotating itself with the marker."""
    f = tmp_path / "spec.md"
    smuggled = "aws key AKIA" + "ABCDEFGHIJKLMNOP" + " here  # forgeops:allow-secret"
    f.write_text(smuggled, encoding="utf-8")
    plan = build_task_create_plan(initialized_repo, "My Task", f, None, MAX_BYTES)
    assert any(c.key == "spec-file-secret-detected" for c in plan.conflicts)


def test_spec_file_content_preserved_verbatim(initialized_repo, tmp_path):
    f = tmp_path / "spec.md"
    f.write_text("# Custom Spec\n\nfreeform\n", encoding="utf-8")
    plan = build_task_create_plan(initialized_repo, "My Task", f, None, MAX_BYTES)
    assert plan.spec_content == "# Custom Spec\n\nfreeform\n"


def test_acceptance_file_nonexistent_is_a_conflict(initialized_repo, tmp_path):
    plan = build_task_create_plan(initialized_repo, "My Task", None, tmp_path / "nope.txt", MAX_BYTES)
    assert any(c.key == "acceptance-file-invalid" for c in plan.conflicts)


def test_acceptance_file_script_rejected(initialized_repo, tmp_path):
    f = tmp_path / "acceptance.sh"
    f.write_text("echo hi", encoding="utf-8")
    plan = build_task_create_plan(initialized_repo, "My Task", None, f, MAX_BYTES)
    assert any(c.key == "acceptance-file-is-script" for c in plan.conflicts)


def test_acceptance_file_secret_content_rejected(initialized_repo, tmp_path):
    f = tmp_path / "acceptance.txt"
    f.write_text("token: AKIAABCDEFGHIJKLMNOP", encoding="utf-8")  # forgeops:allow-secret
    plan = build_task_create_plan(initialized_repo, "My Task", None, f, MAX_BYTES)
    assert any(c.key == "acceptance-file-secret-detected" for c in plan.conflicts)


def test_acceptance_file_merged_into_default_template(initialized_repo, tmp_path):
    f = tmp_path / "acceptance.txt"
    f.write_text("- criterion one\n- criterion two\n", encoding="utf-8")
    plan = build_task_create_plan(initialized_repo, "My Task", None, f, MAX_BYTES)
    assert plan.has_conflict is False
    assert "criterion one" in plan.spec_content
    assert "criterion two" in plan.spec_content


def test_acceptance_file_appended_to_custom_spec_file(initialized_repo, tmp_path):
    spec_f = tmp_path / "spec.md"
    spec_f.write_text("# Custom\n\nfreeform\n", encoding="utf-8")
    acc_f = tmp_path / "acceptance.txt"
    acc_f.write_text("- criterion X\n", encoding="utf-8")
    plan = build_task_create_plan(initialized_repo, "My Task", spec_f, acc_f, MAX_BYTES)
    assert plan.has_conflict is False
    assert "criterion X" in plan.spec_content
    assert "freeform" in plan.spec_content


# --- apply: success -----------------------------------------------------------


def test_apply_creates_task_dir_and_index(initialized_repo):
    plan = build_task_create_plan(initialized_repo, "My Task", None, None, MAX_BYTES)
    outcome = apply_task_create(initialized_repo, plan)
    assert outcome.ok is True
    assert outcome.task_id == "task-0001"
    task_dir = initialized_repo / ".agent" / "tasks" / "task-0001"
    assert (task_dir / "TASK.json").is_file()
    assert (task_dir / "SPEC.md").is_file()
    assert (task_dir / "VALIDATION.json").is_file()
    assert not (task_dir / "RESULT.md").exists()

    index = load_index(initialized_repo)
    assert index.next_task_number == 2
    assert len(index.records) == 1
    assert index.records[0].task_id == "task-0001"
    assert index.records[0].status == "draft"


def test_apply_task_json_initial_fields(initialized_repo):
    plan = build_task_create_plan(initialized_repo, "My Task", None, None, MAX_BYTES)
    apply_task_create(initialized_repo, plan)
    task_dir = initialized_repo / ".agent" / "tasks" / "task-0001"
    load = load_task_record(task_dir)
    rec = load.record
    assert rec.status == "draft"
    assert rec.approval_state == "not_requested"
    assert rec.worktree_id is None
    assert rec.agent_id is None
    assert rec.validation_state == "not_run"
    assert rec.result_state == "pending"
    assert rec.project_root == str(initialized_repo)


def test_apply_validation_json_initial_state(initialized_repo):
    plan = build_task_create_plan(initialized_repo, "My Task", None, None, MAX_BYTES)
    apply_task_create(initialized_repo, plan)
    task_dir = initialized_repo / ".agent" / "tasks" / "task-0001"
    load = load_validation_record(task_dir)
    assert load.record.status == "not_run"


def test_monotonic_task_ids(initialized_repo):
    plan1 = build_task_create_plan(initialized_repo, "Task One", None, None, MAX_BYTES)
    apply_task_create(initialized_repo, plan1)
    plan2 = build_task_create_plan(initialized_repo, "Task Two", None, None, MAX_BYTES)
    outcome2 = apply_task_create(initialized_repo, plan2)
    assert plan1.task_id == "task-0001"
    assert plan2.task_id == "task-0002"
    assert outcome2.task_id == "task-0002"


def test_apply_spacey_repo_path(spacey_initialized_repo):
    plan = build_task_create_plan(spacey_initialized_repo, "My Task", None, None, MAX_BYTES)
    outcome = apply_task_create(spacey_initialized_repo, plan)
    assert outcome.ok is True
    assert (spacey_initialized_repo / ".agent" / "tasks" / "task-0001" / "TASK.json").is_file()


def test_apply_index_is_valid_json(initialized_repo):
    plan = build_task_create_plan(initialized_repo, "My Task", None, None, MAX_BYTES)
    apply_task_create(initialized_repo, plan)
    index_path = initialized_repo / ".agent" / "tasks" / "TASK_INDEX.json"
    json.loads(index_path.read_text(encoding="utf-8"))


# --- apply: failure handling --------------------------------------------------


def test_apply_simulated_write_failure_reports_partial_state(initialized_repo, monkeypatch):
    plan = build_task_create_plan(initialized_repo, "My Task", None, None, MAX_BYTES)

    def boom(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr("forgeops.state.task_create.save_task_record", boom)
    outcome = apply_task_create(initialized_repo, plan)
    assert outcome.ok is False
    assert outcome.partial_state is not None
    task_dir = initialized_repo / ".agent" / "tasks" / "task-0001"
    assert not task_dir.exists()  # rolled back
    index = load_index(initialized_repo)
    assert index.records == []
    assert index.next_task_number == 1


def test_apply_index_write_failure_still_reports_ok(initialized_repo, monkeypatch):
    plan = build_task_create_plan(initialized_repo, "My Task", None, None, MAX_BYTES)
    monkeypatch.setattr(
        "forgeops.state.task_create.save_index",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    outcome = apply_task_create(initialized_repo, plan)
    assert outcome.ok is True
    assert outcome.index_written is False
    assert outcome.index_error is not None
    task_dir = initialized_repo / ".agent" / "tasks" / "task-0001"
    assert (task_dir / "TASK.json").is_file()


def test_apply_never_persists_rejected_secret_content(initialized_repo, tmp_path):
    f = tmp_path / "spec.md"
    f.write_text("token: AKIAABCDEFGHIJKLMNOP", encoding="utf-8")  # forgeops:allow-secret
    plan = build_task_create_plan(initialized_repo, "My Task", f, None, MAX_BYTES)
    assert plan.has_conflict is True
    task_dir = initialized_repo / ".agent" / "tasks" / "task-0001"
    assert not task_dir.exists()
