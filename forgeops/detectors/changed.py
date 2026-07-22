"""Classifies working-tree changes (`forgeops changed`, and the input to
the targeted-test planner) into project area, likely technology, and
broad-impact status, using cheap deterministic filename/path evidence -
never a full language-server-quality dependency graph. See
docs/targeted-testing.md."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from forgeops.core.git import StatusEntry, get_ignored_summary, get_status_entries

AREAS = (
    "frontend", "backend", "tests", "configuration", "dependencies",
    "database", "documentation", "ci-deployment", "infrastructure",
    "security-authentication", "shared-cross-cutting", "unknown",
)

DEPENDENCY_MANIFEST_NAMES = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "requirements.txt", "pyproject.toml", "Pipfile", "poetry.lock",
    "setup.py", "setup.cfg",
}
BUILD_CONFIG_NAMES = {
    "vite.config.js", "vite.config.ts", "vite.config.mjs",
    "webpack.config.js", "webpack.config.ts", "tsconfig.json",
    "babel.config.js", "babel.config.json",
}
TEST_CONFIG_NAMES = {
    "pytest.ini", "jest.config.js", "jest.config.ts", "jest.config.json",
    "vitest.config.js", "vitest.config.ts",
}
CI_CONFIG_NAMES = {".gitlab-ci.yml", "Jenkinsfile", "render.yaml", "vercel.json"}
ROOT_CONFIG_NAMES = {"pyproject.toml", "package.json", "tsconfig.json", "render.yaml", "docker-compose.yml", "docker-compose.yaml"}
ENV_TEMPLATE_SUFFIXES = (".env.example", ".env.template")

TECHNOLOGY_BY_SUFFIX = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "react", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "react",
    ".sql": "sql",
    ".css": "css", ".scss": "css",
    ".html": "html",
    ".md": "documentation",
    ".yml": "yaml", ".yaml": "yaml",
    ".tf": "terraform",
}
TECHNOLOGY_BY_NAME = {
    "package.json": "node", "package-lock.json": "npm", "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn", "requirements.txt": "python", "pyproject.toml": "python",
    "Dockerfile": "docker",
}

_CODE_TO_CATEGORY = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied"}


@dataclass(frozen=True)
class ChangedFile:
    path: str
    old_path: str | None
    category: str  # added | modified | deleted | renamed | copied | untracked | conflicted
    staged: bool
    area: str
    broad_impact: bool
    technology: str | None


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def is_broad_impact(path: str) -> bool:
    name = _basename(path)
    lower = path.lower()
    if name in DEPENDENCY_MANIFEST_NAMES:
        return True
    if name in BUILD_CONFIG_NAMES or name in TEST_CONFIG_NAMES:
        return True
    if ".github/workflows/" in lower or name in CI_CONFIG_NAMES:
        return True
    if "auth" in lower:
        return True
    if "schema" in lower or "contract" in lower:
        return True
    if "/migrations/" in lower or name.endswith(".sql"):
        return True
    if name.endswith(ENV_TEMPLATE_SUFFIXES):
        return True
    if "/" not in path and name in ROOT_CONFIG_NAMES:
        return True
    return False


def classify_area(path: str) -> str:
    name = _basename(path)
    lower = path.lower()

    if name in DEPENDENCY_MANIFEST_NAMES:
        return "dependencies"
    if "auth" in lower or "/security/" in lower:
        return "security-authentication"
    if "/migrations/" in lower or name.endswith(".sql") or "schema" in name.lower():
        return "database"
    if (".github/workflows/" in lower or name in CI_CONFIG_NAMES
            or "dockerfile" in name.lower() or "docker-compose" in name.lower()):
        return "ci-deployment"
    if "terraform" in lower or name.endswith(".tf") or "/infra/" in lower or "/k8s/" in lower or "/kubernetes/" in lower:
        return "infrastructure"
    if (
        name in BUILD_CONFIG_NAMES
        or name in TEST_CONFIG_NAMES
        or name.endswith((".ini", ".cfg"))
        or (name.endswith(".toml") and name != "pyproject.toml")
        or name.startswith(".eslintrc")
    ):
        return "configuration"
    if (
        "/tests/" in lower or "/test/" in lower or "__tests__" in lower
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts"))
    ):
        return "tests"
    if name.endswith((".md", ".rst")) or lower.startswith("docs/"):
        return "documentation"
    # Directory-prefix checks (shared/common/packages, frontend/, backend/)
    # must all run before the generic by-extension fallbacks below, or a
    # file like "shared/schemas/user.py" would be caught by the bare
    # ".py" -> backend fallback before ever reaching this check.
    if lower.startswith(("shared/", "common/", "packages/")):
        return "shared-cross-cutting"
    if lower.startswith("frontend/"):
        return "frontend"
    if lower.startswith("backend/"):
        return "backend"
    if name.endswith((".jsx", ".tsx", ".css", ".scss", ".html")):
        return "frontend"
    if name.endswith(".py"):
        return "backend"
    return "unknown"


def classify_technology(path: str) -> str | None:
    name = _basename(path)
    if name in TECHNOLOGY_BY_NAME:
        return TECHNOLOGY_BY_NAME[name]
    suffix = Path(name).suffix.lower()
    return TECHNOLOGY_BY_SUFFIX.get(suffix)


def _build(path: str, old_path: str | None, category: str, staged: bool) -> ChangedFile:
    return ChangedFile(
        path=path,
        old_path=old_path,
        category=category,
        staged=staged,
        area=classify_area(path),
        broad_impact=is_broad_impact(path),
        technology=classify_technology(path),
    )


def _entries_to_changed_files(entries: list[StatusEntry]) -> list[ChangedFile]:
    files: list[ChangedFile] = []
    for entry in entries:
        if entry.index_state == "?" and entry.worktree_state == "?":
            files.append(_build(entry.path, entry.old_path, "untracked", staged=False))
            continue
        is_conflict = (
            entry.index_state == "U" or entry.worktree_state == "U"
            or (entry.index_state, entry.worktree_state) in (("A", "A"), ("D", "D"))
        )
        if is_conflict:
            files.append(_build(entry.path, entry.old_path, "conflicted", staged=False))
            continue
        if entry.index_state not in (" ", "?"):
            category = _CODE_TO_CATEGORY.get(entry.index_state, "modified")
            files.append(_build(entry.path, entry.old_path, category, staged=True))
        if entry.worktree_state not in (" ", "?"):
            category = _CODE_TO_CATEGORY.get(entry.worktree_state, "modified")
            files.append(_build(entry.path, entry.old_path, category, staged=False))
    return files


@dataclass
class ChangedFilesResult:
    files: list[ChangedFile] = field(default_factory=list)
    ignored_summary: list[str] = field(default_factory=list)

    @property
    def staged(self) -> list[ChangedFile]:
        return [f for f in self.files if f.staged]

    @property
    def unstaged(self) -> list[ChangedFile]:
        return [f for f in self.files if not f.staged and f.category not in ("untracked", "conflicted")]

    @property
    def untracked(self) -> list[ChangedFile]:
        return [f for f in self.files if f.category == "untracked"]

    @property
    def conflicted(self) -> list[ChangedFile]:
        return [f for f in self.files if f.category == "conflicted"]

    @property
    def broad_impact_files(self) -> list[str]:
        return sorted({f.path for f in self.files if f.broad_impact})

    @property
    def has_any_changes(self) -> bool:
        return bool(self.files)

    @property
    def all_paths(self) -> list[str]:
        return sorted({f.path for f in self.files})


def get_changed_files(repo_root: Path) -> ChangedFilesResult:
    entries = get_status_entries(repo_root)
    files = _entries_to_changed_files(entries)
    ignored = get_ignored_summary(repo_root)
    return ChangedFilesResult(files=files, ignored_summary=ignored)
