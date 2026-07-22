"""Single bounded filesystem walk of a repository, classifying every path
into the categories the audit needs (generated artifacts, cache/temp
dirs, nested git repos, env/db/media/browser-state files, oversized
files, dependency manifests, instruction files, stack marker files) plus
a list of files considered safe to open for secret-pattern scanning.

One walk, not one-per-category, so a repo with a large vendor tree is
only traversed once. Known vendor/generated directories are pruned on
sight (never descended into) - their presence is recorded as a single
entry rather than enumerating their contents."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

GENERATED_ARTIFACT_DIRS = {
    "node_modules", "dist", "build", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".venv", "venv", ".next", ".nuxt",
    "coverage", "htmlcov", ".tox", "target",
}
CACHE_TEMP_DIRS = {".cache", "tmp", "temp"}
MEDIA_MODEL_SUFFIXES = {
    ".mp4", ".mov", ".webm", ".png", ".jpg", ".jpeg", ".gif", ".mp3", ".wav",
    ".onnx", ".gguf", ".safetensors", ".pt", ".pth",
}
DB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
ENV_FILE_RE = re.compile(r"^\.env(\..+)?$")
ENV_SAFE_RE = re.compile(r"^.*\.env\.example$")
BROWSER_STATE_NAME_HINTS = ("storagestate", "storage_state", "auth-state", "authstate", "cookies")
DEPENDENCY_MANIFEST_NAMES = {
    "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "Pipfile", "poetry.lock",
}
INSTRUCTION_FILE_NAMES = {"CLAUDE.md", "AGENTS.md"}
STACK_MARKER_NAMES = {
    "pytest.ini", "conftest.py",
    "vite.config.js", "vite.config.ts", "vite.config.mjs",
    "jest.config.js", "jest.config.ts", "jest.config.json", "jest.config.mjs",
    "vitest.config.js", "vitest.config.ts", "vitest.config.mjs",
}


@dataclass
class TreeScan:
    generated_artifact_dirs: list[str] = field(default_factory=list)
    cache_temp_dirs: list[str] = field(default_factory=list)
    nested_git_repos: list[str] = field(default_factory=list)
    media_model_files: list[str] = field(default_factory=list)
    db_files: list[str] = field(default_factory=list)
    env_files_real: list[str] = field(default_factory=list)
    env_files_example: list[str] = field(default_factory=list)
    browser_state_files: list[str] = field(default_factory=list)
    oversized_files: list[tuple[str, int]] = field(default_factory=list)
    dependency_manifests: list[str] = field(default_factory=list)
    instruction_files: list[str] = field(default_factory=list)
    stack_marker_files: list[str] = field(default_factory=list)
    scannable_text_files: list[str] = field(default_factory=list)


def scan_repo_tree(repo_root: Path, oversized_bytes: int, secret_scan_max_bytes: int) -> TreeScan:
    scan = TreeScan()
    root_git = repo_root / ".git"
    stack = [repo_root]

    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            continue

        for entry in entries:
            try:
                rel = entry.relative_to(repo_root).as_posix()
            except ValueError:
                continue

            if entry.is_dir():
                if entry.name in GENERATED_ARTIFACT_DIRS or entry.name.endswith(".egg-info"):
                    scan.generated_artifact_dirs.append(rel)
                    continue
                if entry.name in CACHE_TEMP_DIRS:
                    scan.cache_temp_dirs.append(rel)
                    continue
                if entry.name == ".git":
                    if entry != root_git:
                        scan.nested_git_repos.append(entry.parent.relative_to(repo_root).as_posix())
                    continue
                stack.append(entry)
                continue

            # Regular file.
            name = entry.name
            name_lower = name.lower()
            suffix = entry.suffix.lower()
            sensitive = False

            if ENV_SAFE_RE.match(name):
                scan.env_files_example.append(rel)
                sensitive = True
            elif ENV_FILE_RE.match(name):
                scan.env_files_real.append(rel)
                sensitive = True

            if suffix in DB_SUFFIXES:
                scan.db_files.append(rel)
                sensitive = True

            if suffix in MEDIA_MODEL_SUFFIXES:
                scan.media_model_files.append(rel)
                sensitive = True

            # Checked against the full relative path, not just the
            # filename, since some hints (auth-state/) are directory
            # names rather than filename fragments.
            if any(hint in rel.lower() for hint in BROWSER_STATE_NAME_HINTS):
                scan.browser_state_files.append(rel)
                sensitive = True

            if name in DEPENDENCY_MANIFEST_NAMES:
                scan.dependency_manifests.append(rel)

            if name in INSTRUCTION_FILE_NAMES:
                scan.instruction_files.append(rel)

            if name in STACK_MARKER_NAMES:
                scan.stack_marker_files.append(rel)

            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            if size > oversized_bytes:
                scan.oversized_files.append((rel, size))

            if not sensitive and size <= secret_scan_max_bytes:
                scan.scannable_text_files.append(rel)

    return scan
