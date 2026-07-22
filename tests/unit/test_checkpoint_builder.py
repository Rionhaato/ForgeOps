"""Tests for forgeops/state/checkpoint.py: the pure, deterministic
CURRENT_STATE.json document builder shared by `forgeops checkpoint` and
`forgeops handoff`."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from forgeops.state.checkpoint import (
    SUPPORTED_STATE_SCHEMA_VERSIONS,
    build_checkpoint_data,
    load_previous_state,
)


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def test_load_previous_state_missing_file_returns_defaults(tmp_path):
    result = load_previous_state(tmp_path / "does-not-exist.json")
    assert result.warning is None
    assert result.narrative["mission"] == ""
    assert result.narrative["completed_work"] == []


def test_load_previous_state_valid_file_preserves_narrative(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "schema_version": 1, "branch": "old-branch", "mission": "build the thing",
        "completed_work": ["phase 1"], "blockers": [{"description": "x", "severity": "low", "affects": "y"}],
        "next_action": "do the next part",
    }), encoding="utf-8")
    result = load_previous_state(path)
    assert result.warning is None
    assert result.narrative["mission"] == "build the thing"
    assert result.narrative["completed_work"] == ["phase 1"]
    assert result.narrative["next_action"] == "do the next part"


def test_load_previous_state_invalid_json_falls_back_with_warning(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ not valid json", encoding="utf-8")
    result = load_previous_state(path)
    assert result.warning is not None
    assert "not valid JSON" in result.warning
    assert result.narrative["mission"] == ""


def test_load_previous_state_non_object_top_level_falls_back_with_warning(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    result = load_previous_state(path)
    assert result.warning is not None
    assert "not an object" in result.warning


def test_load_previous_state_unsupported_schema_version_falls_back_with_warning(tmp_path):
    path = tmp_path / "state.json"
    future_version = max(SUPPORTED_STATE_SCHEMA_VERSIONS) + 1
    path.write_text(json.dumps({"schema_version": future_version, "mission": "from the future"}), encoding="utf-8")
    result = load_previous_state(path)
    assert result.warning is not None
    assert "schema_version" in result.warning
    assert result.narrative["mission"] == ""  # not guessed at from the unsupported document


def test_load_previous_state_missing_schema_version_falls_back_with_warning(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"mission": "no version field"}), encoding="utf-8")
    result = load_previous_state(path)
    assert result.warning is not None


def test_build_checkpoint_data_clean_repo(git_repo):
    previous = load_previous_state(git_repo / ".agent" / "CURRENT_STATE.json")
    doc = build_checkpoint_data(git_repo, previous)
    assert doc["schema_version"] == 1
    assert doc["branch"] is not None
    assert doc["head"] is not None
    assert doc["changed_files"] == {"staged": 0, "modified": 0, "untracked": 0}
    assert doc["has_local_changes"] is False
    assert doc["remote"]["configured"] is False


def test_build_checkpoint_data_dirty_repo_staged(git_repo):
    (git_repo / "new_staged.txt").write_text("x", encoding="utf-8")
    _git(["add", "new_staged.txt"], git_repo)
    previous = load_previous_state(git_repo / ".agent" / "CURRENT_STATE.json")
    doc = build_checkpoint_data(git_repo, previous)
    assert doc["changed_files"]["staged"] == 1
    assert doc["has_local_changes"] is True


def test_build_checkpoint_data_dirty_repo_modified(git_repo):
    (git_repo / "README.md").write_text("modified content", encoding="utf-8")
    previous = load_previous_state(git_repo / ".agent" / "CURRENT_STATE.json")
    doc = build_checkpoint_data(git_repo, previous)
    assert doc["changed_files"]["modified"] == 1
    assert doc["has_local_changes"] is True


def test_build_checkpoint_data_untracked_files(git_repo):
    (git_repo / "untracked.txt").write_text("x", encoding="utf-8")
    previous = load_previous_state(git_repo / ".agent" / "CURRENT_STATE.json")
    doc = build_checkpoint_data(git_repo, previous)
    assert doc["changed_files"]["untracked"] == 1
    assert doc["has_local_changes"] is True


def test_build_checkpoint_data_repo_path_with_spaces(spacey_git_repo):
    previous = load_previous_state(spacey_git_repo / ".agent" / "CURRENT_STATE.json")
    doc = build_checkpoint_data(spacey_git_repo, previous)
    assert doc["branch"] is not None
    assert doc["head"] is not None


def test_build_checkpoint_data_no_commits_yet(bare_git_repo):
    previous = load_previous_state(bare_git_repo / ".agent" / "CURRENT_STATE.json")
    doc = build_checkpoint_data(bare_git_repo, previous)
    assert doc["head"] is None


def test_build_checkpoint_data_detects_python_stack(git_repo):
    (git_repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-q", "-m", "add manifest"], git_repo)
    previous = load_previous_state(git_repo / ".agent" / "CURRENT_STATE.json")
    doc = build_checkpoint_data(git_repo, previous)
    technologies = {s["technology"] for s in doc["detected_stack"]}
    assert "python" in technologies


def test_build_checkpoint_data_preserves_narrative_fields(git_repo):
    state_path = git_repo / ".agent" / "CURRENT_STATE.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "schema_version": 1, "branch": "stale", "mission": "the real mission",
        "completed_work": ["step a", "step b"], "blockers": [], "next_action": "step c",
    }), encoding="utf-8")
    previous = load_previous_state(state_path)
    doc = build_checkpoint_data(git_repo, previous)
    assert doc["mission"] == "the real mission"
    assert doc["completed_work"] == ["step a", "step b"]
    assert doc["next_action"] == "step c"
    # Objective fields are recomputed fresh, not carried forward from the stale document.
    assert doc["branch"] != "stale"


def test_build_checkpoint_data_never_includes_secret_shaped_env_values(monkeypatch, git_repo):
    monkeypatch.setenv("SOME_SECRET_TOKEN", "super-secret-value")  # forgeops:allow-secret
    previous = load_previous_state(git_repo / ".agent" / "CURRENT_STATE.json")
    doc = build_checkpoint_data(git_repo, previous)
    serialized = json.dumps(doc)
    assert "super-secret-value" not in serialized
