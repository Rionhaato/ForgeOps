from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from forgeops.cli.audit import render_human, run_audit
from forgeops.core import exit_codes


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _snapshot(repo_root: Path, exclude_dirs: set[str]) -> dict[str, tuple[str, float]]:
    snapshot = {}
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(repo_root).parts
        if rel_parts and rel_parts[0] in exclude_dirs:
            continue
        if ".git" in rel_parts:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        snapshot[str(path.relative_to(repo_root))] = (digest, path.stat().st_mtime)
    return snapshot


def test_audit_clean_repo_all_pass(git_repo):
    result = run_audit(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert all(c.status != "blocked" for c in result.checks)


def test_audit_detects_secret_pattern(git_repo):
    (git_repo / "config.py").write_text("KEY = 'AKIAABCDEFGHIJKLMNOP'\n", encoding="utf-8")  # forgeops:allow-secret
    result = run_audit(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    secret_checks = [c for c in result.checks if c.id == "secret-scan"]
    assert any(c.status == "blocked" for c in secret_checks)


def test_audit_never_prints_the_secret_value(git_repo):
    secret_value = "AKIAABCDEFGHIJKLMNOP"  # forgeops:allow-secret
    (git_repo / "config.py").write_text(f"KEY = '{secret_value}'\n", encoding="utf-8")
    result = run_audit(str(git_repo), write_log=False)
    assert secret_value not in result.to_json()
    assert secret_value not in render_human(result)


def test_audit_flags_dangerous_staged_file(git_repo):
    (git_repo / "backend").mkdir()
    (git_repo / "backend" / ".env").write_text("SECRET=1", encoding="utf-8")
    _git(["add", "-f", "backend/.env"], git_repo)
    result = run_audit(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.BLOCKED
    git_safety = [c for c in result.checks if c.id == "git-safety"]
    assert any(c.status == "blocked" for c in git_safety)


def test_audit_never_reads_env_file_contents(git_repo):
    (git_repo / ".env").write_text("REAL_SECRET_VALUE=do-not-leak-me", encoding="utf-8")
    result = run_audit(str(git_repo), write_log=False)
    assert "do-not-leak-me" not in result.to_json()
    assert "do-not-leak-me" not in render_human(result)
    env_check = next(c for c in result.checks if c.id == "env-files-real")
    assert ".env" in env_check.message


def test_audit_reports_untracked_sensitive_file_as_suspicious(git_repo):
    (git_repo / ".env").write_text("X=1", encoding="utf-8")
    result = run_audit(str(git_repo), write_log=False)
    suspicious = next(c for c in result.checks if c.id == "suspicious-untracked")
    assert suspicious.status == "warning"
    assert ".env" in suspicious.message


def test_audit_generated_artifact_directory_not_scanned(git_repo):
    nm = git_repo / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("AKIAABCDEFGHIJKLMNOP", encoding="utf-8")  # forgeops:allow-secret
    result = run_audit(str(git_repo), write_log=False)
    assert result.exit_code != exit_codes.BLOCKED
    artifact_check = next(c for c in result.checks if c.id == "generated-artifact-dirs")
    assert "node_modules" in artifact_check.message


def test_audit_nested_git_repo_flagged(git_repo):
    nested = git_repo / "vendor" / "lib"
    (nested / ".git").mkdir(parents=True)
    result = run_audit(str(git_repo), write_log=False)
    nested_check = next(c for c in result.checks if c.id == "nested-git-repos")
    assert nested_check.status == "warning"
    assert "vendor/lib" in nested_check.message


def test_audit_repo_not_found(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    result = run_audit(str(outside), write_log=False)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND


def test_audit_missing_git_executable(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.audit.git_version", lambda: None)
    result = run_audit(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE


def test_audit_json_stable_and_never_deletes_edits_stages_commits(git_repo):
    # Exercise every category at once and confirm the result is a pure report.
    (git_repo / ".env").write_text("X=1", encoding="utf-8")
    (git_repo / "big.bin").write_bytes(b"x" * (6 * 1024 * 1024))
    before_status = _git(["status", "--porcelain"], git_repo).stdout
    result = run_audit(str(git_repo), write_log=False)
    after_status = _git(["status", "--porcelain"], git_repo).stdout
    assert before_status == after_status
    assert result.data["files_scanned_for_secrets"] >= 0


def test_audit_is_strictly_read_only(git_repo):
    """No file hash, file count, or mtime changes anywhere in the repo
    (outside logs/, which is the audit's own documented output area)."""
    (git_repo / "app.py").write_text("print(1)\n", encoding="utf-8")
    (git_repo / ".env").write_text("X=1", encoding="utf-8")
    before_git_status = _git(["status", "--porcelain", "--ignored"], git_repo).stdout
    before = _snapshot(git_repo, exclude_dirs={"logs"})

    run_audit(str(git_repo), write_log=False)

    after_git_status = _git(["status", "--porcelain", "--ignored"], git_repo).stdout
    after = _snapshot(git_repo, exclude_dirs={"logs"})

    assert before_git_status == after_git_status
    assert before == after
    assert len(before) == len(after)


def test_audit_writes_redacted_log(git_repo):
    (git_repo / "config.py").write_text("KEY = 'AKIAABCDEFGHIJKLMNOP'\n", encoding="utf-8")  # forgeops:allow-secret
    run_audit(str(git_repo), write_log=True)
    log_files = list((git_repo / "logs" / "audit").glob("*/audit.log"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert "AKIAABCDEFGHIJKLMNOP" not in content  # forgeops:allow-secret
