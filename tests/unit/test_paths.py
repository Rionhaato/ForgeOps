from __future__ import annotations

import pytest

from forgeops.core.paths import (
    RepoNotFoundError,
    find_repo_root,
    is_protected_reference_path,
    normalize_path,
    resolve_repo_root,
)


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


# --- is_protected_reference_path ----------------------------------------
# The real TrendForge checkout is never used as a test target (see
# CLAUDE.md section 6 / repo instructions) - every test here monkeypatches
# READONLY_REFERENCE_REPO to a fake, nonexistent path under tmp_path so the
# guard's own string-comparison logic is exercised without ever touching
# (or requiring the existence of) the real reference repository.
def test_protected_path_exact_match(tmp_path, monkeypatch):
    fake_reference = tmp_path / "FakeTrendForge"
    monkeypatch.setattr("forgeops.core.paths.READONLY_REFERENCE_REPO", fake_reference)
    assert is_protected_reference_path(fake_reference) is True


def test_protected_path_subdirectory(tmp_path, monkeypatch):
    fake_reference = tmp_path / "FakeTrendForge"
    monkeypatch.setattr("forgeops.core.paths.READONLY_REFERENCE_REPO", fake_reference)
    assert is_protected_reference_path(fake_reference / "backend" / "main.py") is True


def test_protected_path_is_case_insensitive(tmp_path, monkeypatch):
    fake_reference = tmp_path / "FakeTrendForge"
    monkeypatch.setattr("forgeops.core.paths.READONLY_REFERENCE_REPO", fake_reference)
    upper = tmp_path / "FAKETRENDFORGE" / "sub"
    assert is_protected_reference_path(upper) is True


def test_unrelated_path_is_not_protected(tmp_path, monkeypatch):
    fake_reference = tmp_path / "FakeTrendForge"
    monkeypatch.setattr("forgeops.core.paths.READONLY_REFERENCE_REPO", fake_reference)
    assert is_protected_reference_path(tmp_path / "SomeOtherProject") is False


def test_sibling_with_shared_prefix_is_not_protected(tmp_path, monkeypatch):
    # "FakeTrendForge2" must not be treated as beneath "FakeTrendForge" -
    # a naive startswith() without the trailing separator would get this
    # wrong.
    fake_reference = tmp_path / "FakeTrendForge"
    monkeypatch.setattr("forgeops.core.paths.READONLY_REFERENCE_REPO", fake_reference)
    assert is_protected_reference_path(tmp_path / "FakeTrendForge2") is False


def test_real_trendforge_path_constant_is_protected_by_default():
    # The actual default constant, never touching the filesystem - proves
    # the shipped default really does cover the real read-only reference
    # repository named in CLAUDE.md, without this test needing that
    # directory to exist.
    assert is_protected_reference_path(normalize_path(r"C:\Users\joshd\TrendForge\some\nested\file.py"))
