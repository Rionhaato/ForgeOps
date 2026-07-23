"""Deterministic name validation and path/branch derivation for
`forgeops worktree create`. Every function here is pure (no filesystem
or git access) so the same NAME always maps to the same branch and
path, and an invalid NAME is always rejected outright rather than
silently rewritten - see docs/worktrees.md "Naming rules"."""
from __future__ import annotations

import re
from pathlib import Path

# A NAME must be a single safe path segment: starts with a letter or
# digit, then any run of letters/digits/hyphen/underscore. This
# deliberately excludes `/`, `\`, `:`, `.`, spaces, and every other
# character that could make an absolute path, a traversal sequence
# (`..`), or a Windows drive/UNC prefix look like a valid NAME - reject
# rather than strip, so a rejected NAME never silently becomes a
# different, unintended NAME.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
MAX_NAME_LENGTH = 100

# Reserved on Windows regardless of extension/case - a directory or file
# literally named one of these cannot be created.
_WINDOWS_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})

MANAGED_ROOT_DIRNAME = ".forgeops-worktrees"
BRANCH_PREFIX = "forgeops/"


def validate_worktree_name(name: str) -> str | None:
    """Return None if `name` is a valid, unambiguous worktree NAME, or a
    human-readable rejection reason otherwise. Never mutates `name`."""
    if not name:
        return "name must not be empty"
    if len(name) > MAX_NAME_LENGTH:
        return f"name must be at most {MAX_NAME_LENGTH} characters"
    if Path(name).is_absolute():
        return "name must not be an absolute path"
    if not _NAME_RE.match(name):
        return (
            "name must start with a letter or digit and contain only "
            "letters, digits, '-', or '_' (rejected rather than "
            "sanitized, to avoid silently changing its meaning)"
        )
    if name.upper() in _WINDOWS_RESERVED_NAMES:
        return f"'{name}' is a reserved Windows device name and cannot be used as a directory name"
    return None


def branch_name_for(sanitized_name: str) -> str:
    """The deterministic ForgeOps-owned branch name derived from a
    validated NAME when `--branch` is omitted: `forgeops/<name>`."""
    return f"{BRANCH_PREFIX}{sanitized_name}"


def managed_root_for(repo_root: Path) -> Path:
    """The deterministic ForgeOps-managed worktree root for a repository:
    `<repository-parent>/.forgeops-worktrees/<repository-name>/`. A
    sibling of the repository itself, never inside it - so an ordinary
    `git status` in the source checkout never sees worktree scaffolding."""
    return repo_root.parent / MANAGED_ROOT_DIRNAME / repo_root.name


def worktree_path_for(repo_root: Path, sanitized_name: str) -> Path:
    """The deterministic destination for a new worktree: the managed
    root plus the (already-validated) sanitized name as a single path
    segment."""
    return managed_root_for(repo_root) / sanitized_name
