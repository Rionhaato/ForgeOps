"""Repository-root discovery and path normalization. Works from a nested
directory, an explicitly supplied path, and paths containing spaces (all
handled natively by pathlib - no string-splitting on path separators)."""
from __future__ import annotations

import os
from pathlib import Path


class RepoNotFoundError(Exception):
    """Raised when no git repository root can be discovered."""


# Mirrors CLAUDE.md section 6 and the hardcoded constant in
# `.claude/hooks/pretooluse_safety.py` (TRENDFORGE_PATH_MARKERS) - the
# one read-only reference repository this toolkit must never write to.
# Deliberately duplicated rather than shared: the hook and this module
# are two independent enforcement layers (hook = Claude Code tool-call
# interception, this = `forgeops init`'s own target-path validation), and
# neither should depend on the other still being wired up correctly.
# Kept a plain module attribute, not read from project config, so a
# project's own `pyproject.toml` can never disable this specific rule.
READONLY_REFERENCE_REPO = Path(r"C:\Users\joshd\TrendForge")


def is_protected_reference_path(target: Path) -> bool:
    """True if `target` is the read-only reference repository itself, or
    any path beneath it. A pure string comparison (case-insensitive via
    os.path.normcase, matching Windows path semantics) - it never touches
    the filesystem, so it can safely run before any existence check and
    is exercised in tests via a monkeypatched READONLY_REFERENCE_REPO
    rather than the real TrendForge checkout."""
    target_norm = os.path.normcase(os.path.normpath(str(target)))
    reference_norm = os.path.normcase(os.path.normpath(str(READONLY_REFERENCE_REPO)))
    return target_norm == reference_norm or target_norm.startswith(reference_norm + os.sep)


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
