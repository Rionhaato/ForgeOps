from __future__ import annotations

import pytest

from forgeops.core.paths import RepoNotFoundError, find_repo_root, resolve_repo_root


def test_find_repo_root_at_root(git_repo):
    assert find_repo_root(git_repo) == git_repo


def test_find_repo_root_from_nested_directory(git_repo):
    nested = git_repo / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) == git_repo


def test_find_repo_root_from_file_inside_repo(git_repo):
    readme = git_repo / "README.md"
    assert find_repo_root(readme) == git_repo


def test_find_repo_root_returns_none_outside_any_repo(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    assert find_repo_root(outside) is None


def test_resolve_repo_root_explicit_path(git_repo):
    assert resolve_repo_root(str(git_repo), cwd=None) == git_repo


def test_resolve_repo_root_explicit_nested_path(git_repo):
    nested = git_repo / "sub"
    nested.mkdir()
    assert resolve_repo_root(str(nested), cwd=None) == git_repo


def test_resolve_repo_root_from_cwd(git_repo):
    assert resolve_repo_root(None, cwd=git_repo) == git_repo


def test_resolve_repo_root_raises_when_not_found(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    with pytest.raises(RepoNotFoundError):
        resolve_repo_root(None, cwd=outside)


def test_resolve_repo_root_raises_for_nonexistent_explicit_path(tmp_path):
    with pytest.raises(RepoNotFoundError):
        resolve_repo_root(str(tmp_path / "does-not-exist"), cwd=None)


def test_spacey_path_repo_root(spacey_git_repo):
    assert "a repo with spaces" in str(spacey_git_repo)
    assert find_repo_root(spacey_git_repo) == spacey_git_repo
    nested = spacey_git_repo / "nested dir"
    nested.mkdir()
    assert find_repo_root(nested) == spacey_git_repo
