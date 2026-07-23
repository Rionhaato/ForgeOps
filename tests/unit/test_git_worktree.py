"""Tests for forgeops/worktrees/git_worktree.py: porcelain parsing
(pure) plus real git invocation against disposable repositories."""
from __future__ import annotations

import subprocess
from pathlib import Path

from forgeops.worktrees.git_worktree import (
    add_worktree,
    branch_exists,
    is_bare_repository,
    list_worktrees,
    parse_worktree_porcelain,
    resolve_commit,
)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


# --- parse_worktree_porcelain (pure) ---------------------------------------


def test_parse_single_primary_checkout():
    text = (
        "worktree /repo\n"
        "HEAD abc123\n"
        "branch refs/heads/main\n"
    )
    entries, warnings = parse_worktree_porcelain(text)
    assert warnings == []
    assert len(entries) == 1
    e = entries[0]
    assert e.path == "/repo"
    assert e.head == "abc123"
    assert e.branch == "main"
    assert not e.detached and not e.bare and not e.locked and not e.prunable


def test_parse_multiple_worktrees():
    text = (
        "worktree /repo\n"
        "HEAD abc123\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /repo-linked\n"
        "HEAD def456\n"
        "branch refs/heads/feature\n"
    )
    entries, warnings = parse_worktree_porcelain(text)
    assert warnings == []
    assert [e.path for e in entries] == ["/repo", "/repo-linked"]
    assert entries[1].branch == "feature"


def test_parse_path_containing_spaces():
    text = "worktree C:\\a repo with spaces\\linked\nHEAD abc123\nbranch refs/heads/main\n"
    entries, _ = parse_worktree_porcelain(text)
    assert entries[0].path == "C:\\a repo with spaces\\linked"


def test_parse_detached_worktree():
    text = "worktree /repo\nHEAD abc123\ndetached\n"
    entries, warnings = parse_worktree_porcelain(text)
    assert warnings == []
    assert entries[0].detached is True
    assert entries[0].branch is None


def test_parse_bare_repository():
    text = "worktree /repo.git\nbare\n"
    entries, warnings = parse_worktree_porcelain(text)
    assert warnings == []
    assert entries[0].bare is True
    assert entries[0].head is None


def test_parse_locked_without_reason():
    text = "worktree /repo-linked\nHEAD abc123\nbranch refs/heads/x\nlocked\n"
    entries, _ = parse_worktree_porcelain(text)
    assert entries[0].locked is True
    assert entries[0].locked_reason is None


def test_parse_locked_with_reason():
    text = "worktree /repo-linked\nHEAD abc123\nbranch refs/heads/x\nlocked reason text\n"
    entries, _ = parse_worktree_porcelain(text)
    assert entries[0].locked is True
    assert entries[0].locked_reason == "reason text"


def test_parse_prunable_with_reason():
    text = "worktree /repo-linked\nHEAD abc123\nbranch refs/heads/x\nprunable gitdir file gone\n"
    entries, _ = parse_worktree_porcelain(text)
    assert entries[0].prunable is True
    assert entries[0].prunable_reason == "gitdir file gone"


def test_parse_malformed_output_degrades_to_warning_not_crash():
    text = "not a worktree line at all\nsomething else\n"
    entries, warnings = parse_worktree_porcelain(text)
    assert entries == []
    assert len(warnings) == 2


def test_parse_unrecognized_line_within_entry_is_a_warning_not_fatal():
    text = "worktree /repo\nHEAD abc123\nbranch refs/heads/main\nsomething-new-git-added\n"
    entries, warnings = parse_worktree_porcelain(text)
    assert len(entries) == 1
    assert len(warnings) == 1


def test_parse_empty_output():
    entries, warnings = parse_worktree_porcelain("")
    assert entries == []
    assert warnings == []


# --- real git invocation ----------------------------------------------------


def test_list_worktrees_primary_only(git_repo):
    result = list_worktrees(git_repo)
    assert result.ok is True
    assert len(result.entries) == 1
    assert Path(result.entries[0].path).resolve() == git_repo.resolve()


def test_list_worktrees_with_additional_worktree(git_repo, tmp_path):
    linked = tmp_path / "linked"
    _git(["worktree", "add", "-b", "feature", str(linked)], cwd=git_repo)
    result = list_worktrees(git_repo)
    assert result.ok is True
    assert len(result.entries) == 2
    paths = {Path(e.path).resolve() for e in result.entries}
    assert linked.resolve() in paths


def test_list_worktrees_on_non_git_directory(tmp_path):
    result = list_worktrees(tmp_path)
    assert result.ok is False
    assert result.error


def test_is_bare_repository_false_for_normal_repo(git_repo):
    assert is_bare_repository(git_repo) is False


def test_branch_exists_true_and_false(git_repo):
    assert branch_exists(git_repo, "does-not-exist") is False
    _git(["branch", "some-branch"], cwd=git_repo)
    assert branch_exists(git_repo, "some-branch") is True


def test_resolve_commit_head(git_repo):
    head = _git(["rev-parse", "HEAD"], cwd=git_repo).stdout.strip()
    assert resolve_commit(git_repo, "HEAD") == head


def test_resolve_commit_missing_ref(git_repo):
    assert resolve_commit(git_repo, "does-not-exist-ref") is None


def test_add_worktree_creates_new_branch_and_checkout(git_repo, tmp_path):
    dest = tmp_path / "new-worktree"
    head = _git(["rev-parse", "HEAD"], cwd=git_repo).stdout.strip()
    proc = add_worktree(git_repo, dest, "forgeops/demo", head)
    assert proc.ok is True
    assert dest.is_dir()
    assert branch_exists(git_repo, "forgeops/demo") is True


def test_add_worktree_spacey_repo_and_destination(spacey_git_repo, tmp_path):
    dest = tmp_path / "a worktree with spaces"
    head = _git(["rev-parse", "HEAD"], cwd=spacey_git_repo).stdout.strip()
    proc = add_worktree(spacey_git_repo, dest, "forgeops/spacey", head)
    assert proc.ok is True
    assert dest.is_dir()
