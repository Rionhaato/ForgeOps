from __future__ import annotations

import json
import subprocess

from forgeops.cli.changed import render_human, run_changed
from forgeops.core import exit_codes


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def test_clean_repo(git_repo):
    result = run_changed(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["has_any_changes"] is False


def test_staged_filter(git_repo):
    (git_repo / "staged.py").write_text("x = 1", encoding="utf-8")
    _git(["add", "staged.py"], git_repo)
    (git_repo / "untracked.py").write_text("y = 2", encoding="utf-8")
    result = run_changed(str(git_repo), write_log=False, staged=True)
    assert len(result.data["staged"]) == 1
    assert result.data["untracked"] == []


def test_unstaged_filter(git_repo):
    (git_repo / "README.md").write_text("changed\n", encoding="utf-8")
    (git_repo / "new.py").write_text("y = 2", encoding="utf-8")
    result = run_changed(str(git_repo), write_log=False, unstaged=True)
    assert len(result.data["unstaged"]) == 1
    assert result.data["untracked"] == []


def test_untracked_filter(git_repo):
    (git_repo / "README.md").write_text("changed\n", encoding="utf-8")
    (git_repo / "new.py").write_text("y = 2", encoding="utf-8")
    result = run_changed(str(git_repo), write_log=False, untracked=True)
    assert len(result.data["untracked"]) == 1
    assert result.data["unstaged"] == []


def test_default_shows_everything(git_repo):
    (git_repo / "README.md").write_text("changed\n", encoding="utf-8")
    (git_repo / "new.py").write_text("y = 2", encoding="utf-8")
    result = run_changed(str(git_repo), write_log=False)
    assert len(result.data["unstaged"]) == 1
    assert len(result.data["untracked"]) == 1


def test_repo_not_found(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    result = run_changed(str(outside), write_log=False)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND


def test_changed_never_mutates_repo(git_repo):
    (git_repo / "README.md").write_text("changed\n", encoding="utf-8")
    before = _git(["status", "--porcelain"], git_repo)
    run_changed(str(git_repo), write_log=False)
    after = subprocess.run(["git", "status", "--porcelain"], cwd=str(git_repo), capture_output=True, text=True)
    before_out = subprocess.run(["git", "status", "--porcelain"], cwd=str(git_repo), capture_output=True, text=True)
    assert before_out.stdout == after.stdout


def test_json_output_stable(git_repo):
    (git_repo / "new.py").write_text("y = 2", encoding="utf-8")
    clock = lambda: __import__("datetime").datetime(2026, 1, 1)
    a = run_changed(str(git_repo), write_log=False, clock=clock)
    b = run_changed(str(git_repo), write_log=False, clock=clock)
    assert a.to_json() == b.to_json()


def test_human_output_lists_paths(git_repo):
    (git_repo / "new.py").write_text("y = 2", encoding="utf-8")
    result = run_changed(str(git_repo), write_log=False)
    text = render_human(result)
    assert "new.py" in text


def test_broad_impact_flagged(git_repo):
    (git_repo / "package.json").write_text("{}", encoding="utf-8")
    result = run_changed(str(git_repo), write_log=False)
    assert "package.json" in result.data["broad_impact_files"]


def test_areas_touched_summary(git_repo):
    (git_repo / "app.py").write_text("x = 1", encoding="utf-8")
    result = run_changed(str(git_repo), write_log=False)
    assert result.data["areas_touched"].get("backend") == 1
