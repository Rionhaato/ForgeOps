"""Shared pytest fixtures. Every fixture here builds a disposable
repository under pytest's tmp_path - nothing ever touches TrendForge or
the real ForgeOps checkout."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


def init_git_repo(path: Path, *, initial_commit: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-q"], cwd=path)
    _run_git(["config", "user.name", "ForgeOps Test"], cwd=path)
    _run_git(["config", "user.email", "forgeops-test@example.invalid"], cwd=path)
    if initial_commit:
        (path / "README.md").write_text("# disposable test repo\n", encoding="utf-8")
        _run_git(["add", "README.md"], cwd=path)
        _run_git(["commit", "-q", "-m", "initial commit"], cwd=path)
    return path


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A minimal, clean git repo with one commit on the default branch."""
    return init_git_repo(tmp_path / "repo")


@pytest.fixture
def bare_git_repo(tmp_path: Path) -> Path:
    """A git repo with `git init` run but zero commits."""
    return init_git_repo(tmp_path / "repo", initial_commit=False)


@pytest.fixture
def spacey_git_repo(tmp_path: Path) -> Path:
    """A clean git repo whose path contains spaces."""
    return init_git_repo(tmp_path / "a repo with spaces")


def init_forgeops_project(repo: Path) -> Path:
    """Run the real `forgeops init` against an existing git repo, so
    `.agent/CURRENT_STATE.json` etc. exist - the precondition every task-
    engine command requires ("an initialized ForgeOps project")."""
    from forgeops.cli.init import run_init
    result = run_init(str(repo), write_log=False)
    assert result.exit_code == 0
    return repo


@pytest.fixture
def initialized_repo(git_repo: Path) -> Path:
    """A clean git repo that has also been through `forgeops init`."""
    return init_forgeops_project(git_repo)


@pytest.fixture
def spacey_initialized_repo(spacey_git_repo: Path) -> Path:
    """A clean git repo whose path contains spaces, also initialized."""
    return init_forgeops_project(spacey_git_repo)
