from __future__ import annotations

import subprocess
from pathlib import Path

from forgeops.core.git import (
    get_ahead_behind,
    get_branch_state,
    get_head,
    get_ignored_summary,
    get_last_commit,
    get_operation_state,
    get_remotes,
    get_status,
    git_version,
    redact_remote_url,
)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def test_git_version_reports_a_string():
    assert git_version() is not None
    assert "git" in git_version().lower()


def test_branch_state_clean_repo(git_repo):
    state = get_branch_state(git_repo)
    assert state.detached is False
    assert state.has_commits is True
    assert state.branch  # default branch name (master or main)


def test_branch_state_no_commits_yet(bare_git_repo):
    state = get_branch_state(bare_git_repo)
    assert state.has_commits is False
    assert state.detached is False
    # HEAD still symbolically points at a branch name (master/main) even
    # before the first commit exists - only has_commits distinguishes this.
    assert isinstance(state.branch, str)


def test_branch_state_detached_head(git_repo):
    head = get_head(git_repo)
    _git(["checkout", "-q", head], cwd=git_repo)
    state = get_branch_state(git_repo)
    assert state.detached is True
    assert state.branch is None


def test_get_head_matches_rev_parse(git_repo):
    head = get_head(git_repo)
    assert head is not None
    assert len(head) == 40


def test_get_head_none_without_commits(bare_git_repo):
    assert get_head(bare_git_repo) is None


def test_remotes_empty_without_remote(git_repo):
    assert get_remotes(git_repo) == []


def test_remotes_redacts_credentials(git_repo):
    _git(["remote", "add", "origin", "https://user:hunter2@example.invalid/repo.git"], cwd=git_repo)
    remotes = get_remotes(git_repo)
    assert len(remotes) == 1
    assert "hunter2" not in remotes[0].url
    assert "[REDACTED]" in remotes[0].url


def test_redact_remote_url_leaves_ssh_style_untouched():
    url = "git@github.com:example/repo.git"
    assert redact_remote_url(url) == url


def test_ahead_behind_with_real_upstream(tmp_path: Path):
    remote = tmp_path / "remote.git"
    _git(["init", "-q", "--bare", str(remote)], cwd=tmp_path)

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], capture_output=True, text=True, check=True)
    _git(["config", "user.name", "ForgeOps Test"], cwd=clone)
    _git(["config", "user.email", "forgeops-test@example.invalid"], cwd=clone)
    (clone / "a.txt").write_text("a", encoding="utf-8")
    _git(["add", "a.txt"], cwd=clone)
    _git(["commit", "-q", "-m", "first"], cwd=clone)
    _git(["push", "-q", "-u", "origin", "HEAD"], cwd=clone)

    # Local-only commit (ahead by 1).
    (clone / "b.txt").write_text("b", encoding="utf-8")
    _git(["add", "b.txt"], cwd=clone)
    _git(["commit", "-q", "-m", "second"], cwd=clone)

    result = get_ahead_behind(clone)
    assert result.upstream is not None
    assert result.ahead == 1
    assert result.behind == 0


def test_ahead_behind_when_behind_upstream(tmp_path: Path):
    remote = tmp_path / "remote.git"
    _git(["init", "-q", "--bare", str(remote)], cwd=tmp_path)

    seeder = tmp_path / "seeder"
    subprocess.run(["git", "clone", "-q", str(remote), str(seeder)], capture_output=True, text=True, check=True)
    _git(["config", "user.name", "ForgeOps Test"], cwd=seeder)
    _git(["config", "user.email", "forgeops-test@example.invalid"], cwd=seeder)
    (seeder / "a.txt").write_text("a", encoding="utf-8")
    _git(["add", "a.txt"], cwd=seeder)
    _git(["commit", "-q", "-m", "first"], cwd=seeder)
    _git(["push", "-q", "-u", "origin", "HEAD"], cwd=seeder)

    clone = tmp_path / "clone2"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], capture_output=True, text=True, check=True)
    _git(["config", "user.name", "ForgeOps Test"], cwd=clone)
    _git(["config", "user.email", "forgeops-test@example.invalid"], cwd=clone)

    # Someone else pushes a commit the clone hasn't fetched yet.
    (seeder / "b.txt").write_text("b", encoding="utf-8")
    _git(["add", "b.txt"], cwd=seeder)
    _git(["commit", "-q", "-m", "second"], cwd=seeder)
    _git(["push", "-q"], cwd=seeder)
    _git(["fetch", "-q"], cwd=clone)

    result = get_ahead_behind(clone)
    assert result.ahead == 0
    assert result.behind == 1


def test_ahead_behind_no_upstream(git_repo):
    result = get_ahead_behind(git_repo)
    assert result.upstream is None
    assert result.ahead is None
    assert result.behind is None


def test_status_clean_repo(git_repo):
    status = get_status(git_repo)
    assert status.clean
    assert status.staged == []
    assert status.modified == []
    assert status.untracked == []


def test_status_untracked_file(git_repo):
    (git_repo / "new.txt").write_text("hello", encoding="utf-8")
    status = get_status(git_repo)
    assert not status.clean
    assert "new.txt" in status.untracked


def test_status_staged_file(git_repo):
    (git_repo / "staged.txt").write_text("hello", encoding="utf-8")
    _git(["add", "staged.txt"], cwd=git_repo)
    status = get_status(git_repo)
    assert "staged.txt" in status.staged
    assert "staged.txt" not in status.untracked


def test_status_modified_tracked_file(git_repo):
    (git_repo / "README.md").write_text("changed\n", encoding="utf-8")
    status = get_status(git_repo)
    assert "README.md" in status.modified
    assert "README.md" not in status.staged


def test_operation_state_none_normally(git_repo):
    assert get_operation_state(git_repo) == "none"


def test_last_commit_summary(git_repo):
    commit = get_last_commit(git_repo)
    assert commit is not None
    assert commit.subject == "initial commit"
    assert len(commit.sha) == 40


def test_last_commit_none_without_commits(bare_git_repo):
    assert get_last_commit(bare_git_repo) is None


def test_ignored_summary_reports_ignored_paths(git_repo):
    (git_repo / ".gitignore").write_text("ignored-dir/\n", encoding="utf-8")
    ignored_dir = git_repo / "ignored-dir"
    ignored_dir.mkdir()
    (ignored_dir / "file.txt").write_text("x", encoding="utf-8")
    summary = get_ignored_summary(git_repo)
    assert any("ignored-dir" in entry for entry in summary)
