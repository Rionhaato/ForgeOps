"""Tests for forgeops/worktrees/naming.py: NAME validation and
deterministic branch/path derivation for `forgeops worktree create`."""
from __future__ import annotations

from pathlib import Path

from forgeops.worktrees.naming import (
    branch_name_for,
    managed_root_for,
    validate_worktree_name,
    worktree_path_for,
)


def test_simple_name_is_valid():
    assert validate_worktree_name("demo-isolation") is None


def test_alnum_underscore_hyphen_is_valid():
    assert validate_worktree_name("abc_123-XYZ") is None


def test_empty_name_is_rejected():
    assert validate_worktree_name("") is not None


def test_absolute_windows_path_is_rejected():
    assert validate_worktree_name(r"C:\evil") is not None


def test_absolute_posix_path_is_rejected():
    assert validate_worktree_name("/evil") is not None


def test_traversal_sequence_is_rejected():
    assert validate_worktree_name("..") is not None


def test_traversal_like_segment_is_rejected():
    assert validate_worktree_name("..foo") is not None


def test_separator_containing_name_is_rejected():
    assert validate_worktree_name("foo/bar") is not None
    assert validate_worktree_name("foo\\bar") is not None


def test_space_containing_name_is_rejected():
    assert validate_worktree_name("has space") is not None


def test_dot_containing_name_is_rejected():
    assert validate_worktree_name("has.dot") is not None


def test_leading_hyphen_is_rejected():
    # Must start with a letter/digit, not a hyphen/underscore, to stay
    # unambiguous and avoid looking like a CLI flag.
    assert validate_worktree_name("-oops") is not None


def test_name_too_long_is_rejected():
    assert validate_worktree_name("a" * 101) is not None


def test_windows_reserved_device_name_is_rejected():
    assert validate_worktree_name("CON") is not None
    assert validate_worktree_name("con") is not None
    assert validate_worktree_name("COM1") is not None


def test_ambiguous_name_is_rejected_not_rewritten():
    # A name containing a disallowed character must be rejected outright,
    # never silently stripped/transformed into a different valid name.
    error = validate_worktree_name("weird name!")
    assert error is not None


def test_branch_name_for_uses_forgeops_namespace():
    assert branch_name_for("demo-isolation") == "forgeops/demo-isolation"


def test_managed_root_is_sibling_of_repository():
    repo_root = Path("/repos/my-project")
    root = managed_root_for(repo_root)
    assert root == Path("/repos/.forgeops-worktrees/my-project")
    # Never inside the repository itself.
    assert repo_root not in root.parents


def test_worktree_path_is_under_managed_root():
    repo_root = Path("/repos/my-project")
    path = worktree_path_for(repo_root, "demo-isolation")
    assert path == managed_root_for(repo_root) / "demo-isolation"
    assert path.is_relative_to(managed_root_for(repo_root))


def test_managed_root_handles_spacey_repo_path():
    repo_root = Path("/repos/a project with spaces")
    root = managed_root_for(repo_root)
    assert root.name == "a project with spaces"
    assert ".forgeops-worktrees" in root.parts
