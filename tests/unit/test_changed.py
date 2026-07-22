from __future__ import annotations

import subprocess
from pathlib import Path

from forgeops.detectors.changed import classify_area, classify_technology, get_changed_files, is_broad_impact


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def test_clean_repo_has_no_changes(git_repo):
    result = get_changed_files(git_repo)
    assert result.files == []
    assert result.has_any_changes is False


def test_staged_file_detected(git_repo):
    (git_repo / "staged.py").write_text("x = 1", encoding="utf-8")
    _git(["add", "staged.py"], git_repo)
    result = get_changed_files(git_repo)
    staged_paths = {f.path for f in result.staged}
    assert "staged.py" in staged_paths
    assert result.unstaged == []
    assert result.untracked == []


def test_unstaged_modification_detected(git_repo):
    (git_repo / "README.md").write_text("changed\n", encoding="utf-8")
    result = get_changed_files(git_repo)
    unstaged_paths = {f.path for f in result.unstaged}
    assert "README.md" in unstaged_paths
    assert result.staged == []


def test_untracked_file_detected(git_repo):
    (git_repo / "new.py").write_text("x = 1", encoding="utf-8")
    result = get_changed_files(git_repo)
    untracked_paths = {f.path for f in result.untracked}
    assert "new.py" in untracked_paths
    for f in result.untracked:
        assert f.category == "untracked"
        assert f.staged is False


def test_deleted_file_detected(git_repo):
    (git_repo / "README.md").unlink()
    result = get_changed_files(git_repo)
    categories = {f.path: f.category for f in result.files}
    assert categories["README.md"] == "deleted"


def test_renamed_file_detected(git_repo):
    (git_repo / "renamed.md").write_text("# disposable test repo\n", encoding="utf-8")
    (git_repo / "README.md").unlink()
    _git(["add", "-A"], git_repo)
    result = get_changed_files(git_repo)
    renamed = [f for f in result.staged if f.category == "renamed"]
    assert len(renamed) == 1
    assert renamed[0].old_path == "README.md"
    assert renamed[0].path == "renamed.md"


def test_conflicted_file_detected(tmp_path: Path):
    # Build a real merge conflict.
    from tests.conftest import init_git_repo

    repo = init_git_repo(tmp_path / "conflict-repo")
    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    _git(["add", "shared.txt"], repo)
    _git(["commit", "-q", "-m", "base"], repo)
    main_branch = _current_branch(repo)

    _git(["checkout", "-q", "-b", "branch-a"], repo)
    (repo / "shared.txt").write_text("branch-a\n", encoding="utf-8")
    _git(["commit", "-q", "-am", "a"], repo)

    _git(["checkout", "-q", main_branch], repo)
    (repo / "shared.txt").write_text("main\n", encoding="utf-8")
    _git(["commit", "-q", "-am", "main-change"], repo)

    subprocess.run(["git", "merge", "-q", "branch-a"], cwd=str(repo), capture_output=True, text=True)

    result = get_changed_files(repo)
    conflicted_paths = {f.path for f in result.conflicted}
    assert "shared.txt" in conflicted_paths


def _current_branch(repo: Path) -> str:
    out = subprocess.run(["git", "symbolic-ref", "--short", "HEAD"], cwd=str(repo), capture_output=True, text=True)
    return out.stdout.strip()


def test_ignored_generated_artifact_reported_as_summary(git_repo):
    (git_repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (git_repo / "node_modules").mkdir()
    (git_repo / "node_modules" / "pkg.js").write_text("x", encoding="utf-8")
    _git(["add", ".gitignore"], git_repo)
    _git(["commit", "-q", "-m", "add gitignore"], git_repo)
    result = get_changed_files(git_repo)
    assert any("node_modules" in entry for entry in result.ignored_summary)


def test_nested_working_directory(git_repo):
    (git_repo / "a" / "b").mkdir(parents=True)
    (git_repo / "a" / "b" / "new.py").write_text("x = 1", encoding="utf-8")
    result = get_changed_files(git_repo)
    assert "a/b/new.py" in result.all_paths


def test_spacey_path_repo(spacey_git_repo):
    (spacey_git_repo / "new file.py").write_text("x = 1", encoding="utf-8")
    result = get_changed_files(spacey_git_repo)
    assert "new file.py" in result.all_paths


# --- classification ---

def test_classify_area_frontend():
    assert classify_area("frontend/src/App.jsx") == "frontend"


def test_classify_area_backend():
    assert classify_area("backend/app/main.py") == "backend"


def test_classify_area_tests():
    assert classify_area("tests/unit/test_foo.py") == "tests"
    assert classify_area("backend/tests_unit/test_bar.py") == "tests"


def test_classify_area_dependencies():
    assert classify_area("backend/requirements.txt") == "dependencies"
    assert classify_area("frontend/package.json") == "dependencies"


def test_classify_area_configuration():
    assert classify_area("frontend/vite.config.js") == "configuration"


def test_classify_area_database():
    assert classify_area("backend/migrations/0001_init.sql") == "database"


def test_classify_area_authentication():
    assert classify_area("backend/app/api/auth.py") == "security-authentication"


def test_classify_area_cross_cutting():
    assert classify_area("shared/schemas/user.py") == "shared-cross-cutting"


def test_classify_area_ci_deployment():
    assert classify_area(".github/workflows/ci.yml") == "ci-deployment"


def test_classify_area_documentation():
    assert classify_area("docs/architecture.md") == "documentation"


def test_classify_area_unknown():
    assert classify_area("random.xyz") == "unknown"


def test_classify_technology():
    assert classify_technology("app.py") == "python"
    assert classify_technology("Component.tsx") == "react"
    assert classify_technology("package.json") == "node"


def test_is_broad_impact_dependency_manifest():
    assert is_broad_impact("backend/requirements.txt") is True


def test_is_broad_impact_ordinary_source_file():
    assert is_broad_impact("backend/app/services/foo.py") is False


def test_is_broad_impact_ci_config():
    assert is_broad_impact(".github/workflows/ci.yml") is True


def test_is_broad_impact_auth_path():
    assert is_broad_impact("backend/app/api/auth.py") is True
