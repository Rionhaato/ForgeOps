"""Unit tests for `forgeops.state.resume_context.build_resume_context` -
the pure, read-only compact-document builder behind `forgeops
resume-context`. See tests/integration/test_cli_resume_context.py for the
CLI-level (schema/exit-code/render) coverage."""
from __future__ import annotations

import json
from pathlib import Path

from forgeops.state.resume_context import (
    RESUME_CONTEXT_MAX_BYTES,
    build_resume_context,
)


def _write_state(repo: Path, document: dict) -> None:
    state_dir = repo / ".agent"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "CURRENT_STATE.json").write_text(json.dumps(document), encoding="utf-8")


def test_clean_repository_no_state_file(git_repo):
    doc = build_resume_context(git_repo)
    assert doc["working_tree"]["clean"] is True
    assert doc["working_tree"]["modified"] == 0
    assert doc["working_tree"]["untracked"] == 0
    assert doc["phase"] is None
    assert doc["state_paths"]["current_state"] is None
    assert doc["state_paths"]["handoff"] is None
    assert doc["unresolved_blockers"] == []
    assert doc["do_not_repeat"] == []


def test_dirty_repository_reports_file_counts(git_repo):
    (git_repo / "new_file.py").write_text("x = 1\n", encoding="utf-8")
    (git_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    doc = build_resume_context(git_repo)
    assert doc["working_tree"]["clean"] is False
    assert doc["working_tree"]["modified"] == 1
    assert doc["working_tree"]["untracked"] == 1
    assert "README.md" in doc["files_in_progress"]["modified"]["items"]
    assert "new_file.py" in doc["files_in_progress"]["untracked"]["items"]


def test_paths_with_spaces_are_supported(spacey_git_repo):
    doc = build_resume_context(spacey_git_repo)
    assert doc["repository"]["root"] == str(spacey_git_repo)
    assert doc["working_tree"]["clean"] is True


def test_missing_state_file_produces_defaults_not_a_crash(git_repo):
    doc = build_resume_context(git_repo)
    assert doc["phase"] is None
    assert doc["next_action"] is None
    assert "state_warning" not in doc


def test_malformed_state_file_is_reported_as_a_warning_not_a_crash(git_repo):
    state_dir = git_repo / ".agent"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "CURRENT_STATE.json").write_text("{not valid json", encoding="utf-8")
    doc = build_resume_context(git_repo)
    assert "state_warning" in doc
    assert "not valid JSON" in doc["state_warning"]


def test_unsupported_schema_version_is_a_warning(git_repo):
    _write_state(git_repo, {"schema_version": 999, "next_action": "should be discarded"})
    doc = build_resume_context(git_repo)
    assert "state_warning" in doc
    assert doc["next_action"] is None


def test_state_data_is_read_but_current_state_file_still_reported_as_present(git_repo):
    _write_state(git_repo, {
        "schema_version": 1,
        "next_action": "review and commit the pending checkpoint",
        "last_checkpoint": {"phase": "Phase X - example"},
        "blockers": [{"description": "example blocker", "severity": "low"}],
        "recent_tests": [{"command": "python -m pytest tests -q", "result": "42 passed", "timestamp_local": "2026-07-22"}],
    })
    doc = build_resume_context(git_repo)
    assert doc["state_paths"]["current_state"] is not None
    assert doc["phase"] == "Phase X - example"
    assert doc["next_action"] == "review and commit the pending checkpoint"
    assert doc["unresolved_blockers"] == [{"description": "example blocker", "severity": "low"}]
    assert doc["latest_validation"] == {
        "command": "python -m pytest tests -q",
        "result": "42 passed",
        "timestamp_local": "2026-07-22",
    }
    assert doc["do_not_repeat"] == ["python -m pytest tests -q -> 42 passed"]


def test_secret_shaped_next_action_is_redacted(git_repo):
    _write_state(git_repo, {
        "schema_version": 1,
        "next_action": "retry after refreshing Bearer abcdefghijklmnop1234567890 token",  # forgeops:allow-secret
    })
    doc = build_resume_context(git_repo)
    assert "abcdefghijklmnop1234567890" not in doc["next_action"]
    assert "[REDACTED]" in doc["next_action"]


def test_secret_shaped_blocker_description_is_redacted(git_repo):
    _write_state(git_repo, {
        "schema_version": 1,
        "blockers": [{"description": "log leaked Bearer abcdefghijklmnop1234567890 in output", "severity": "high"}],  # forgeops:allow-secret
    })
    doc = build_resume_context(git_repo)
    assert "abcdefghijklmnop1234567890" not in doc["unresolved_blockers"][0]["description"]


def test_bounded_output_size_under_adversarial_narrative(git_repo):
    """A pathologically large CURRENT_STATE.json (many long blockers, a
    huge next_action, many recent_tests entries) must still serialize
    under the strict byte ceiling - the whole point of resume-context."""
    _write_state(git_repo, {
        "schema_version": 1,
        "next_action": "x" * 5000,
        "last_checkpoint": {"phase": "y" * 2000},
        "blockers": [{"description": "z" * 500, "severity": "low"} for _ in range(50)],
        "recent_tests": [
            {"command": f"python -m pytest tests/test_{i}.py -q", "result": "1 passed", "timestamp_local": "2026-07-22"}
            for i in range(50)
        ],
    })
    doc = build_resume_context(git_repo)
    size = len(json.dumps(doc, indent=2).encode("utf-8"))
    assert size <= RESUME_CONTEXT_MAX_BYTES, f"resume-context output was {size} bytes, over the {RESUME_CONTEXT_MAX_BYTES}-byte ceiling"


def test_bounded_lists_report_truncation_and_totals(git_repo):
    _write_state(git_repo, {
        "schema_version": 1,
        "blockers": [{"description": f"blocker {i}", "severity": "low"} for i in range(10)],
    })
    doc = build_resume_context(git_repo)
    assert len(doc["unresolved_blockers"]) == 3
    assert doc["unresolved_blockers_total"] == 10


def test_never_writes_any_file(git_repo):
    before = sorted(p.relative_to(git_repo) for p in git_repo.rglob("*") if p.is_file())
    build_resume_context(git_repo)
    after = sorted(p.relative_to(git_repo) for p in git_repo.rglob("*") if p.is_file())
    assert before == after
