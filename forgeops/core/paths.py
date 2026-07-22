"""Repository-root discovery and path normalization. Works from a nested
directory, an explicitly supplied path, and paths containing spaces (all
handled natively by pathlib - no string-splitting on path separators)."""
from __future__ import annotations

from pathlib import Path


class RepoNotFoundError(Exception):
    """Raised when no git repository root can be discovered."""


def normalize_path(raw: str | Path) -> Path:
    """Resolve a user-supplied path to an absolute Path. Does not require
    the path to exist yet (callers decide whether existence matters)."""
    return Path(raw).expanduser().resolve()


def find_repo_root(start: str | Path) -> Path | None:
    """Walk upward from `start` looking for a `.git` entry (a directory
    for a normal repo, or a file for a linked worktree). Returns the
    directory containing it, or None if no repository is found before
    reaching the filesystem root."""
    current = normalize_path(start)
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def resolve_repo_root(repo_arg: str | None, cwd: str | Path | None = None) -> Path:
    """Resolve the repository root to operate on: an explicit --repo
    argument takes precedence (itself resolved via find_repo_root in case
    it points at a nested directory), otherwise discovery starts at cwd.
    Raises RepoNotFoundError if no repository can be found."""
    start = repo_arg if repo_arg is not None else (cwd if cwd is not None else Path.cwd())
    start_path = normalize_path(start)
    if repo_arg is not None and not start_path.exists():
        raise RepoNotFoundError(f"path does not exist: {start_path}")
    root = find_repo_root(start_path)
    if root is None:
        raise RepoNotFoundError(f"no git repository found at or above: {start_path}")
    return root
