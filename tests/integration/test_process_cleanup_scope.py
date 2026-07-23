"""Regression tests for the process-list/cleanup checkpoint: every
existing command must remain unchanged, `forgeops process-list` must
stay fully read-only, and `forgeops cleanup` must never write or delete
anything outside `.agent/runtime/PROCESS_REGISTRY.json` (plus its own
`logs/cleanup/` entry) - and must never touch a repository other than
the one it was explicitly pointed at (i.e. never TrendForge, or any
other path, regardless of what a mocked discovery reports)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from forgeops.cli.audit import run_audit
from forgeops.cli.changed import run_changed
from forgeops.cli.checkpoint import run_checkpoint
from forgeops.cli.cleanup import run_cleanup
from forgeops.cli.doctor import run_doctor
from forgeops.cli.handoff import run_handoff
from forgeops.cli.process_list import run_process_list
from forgeops.cli.release_check import run_release_check
from forgeops.cli.status import run_status
from forgeops.cli.test import run_full_test, run_test_targeted
from forgeops.core import exit_codes
from forgeops.detectors.processes import ProcessDiscovery, ProcessInfo
from forgeops.state.runtime_registry import RegistryDocument, RegistryRecord


def _discovery(processes):
    return ProcessDiscovery(processes=processes, platform_supported=True, limitation=None)


def _snapshot(root: Path) -> dict[str, str]:
    snapshot = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/") or rel.startswith("logs/"):
            continue
        try:
            snapshot[rel] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            snapshot[rel] = "<unreadable>"
    return snapshot


def test_existing_commands_unchanged_by_this_checkpoint(git_repo):
    assert run_doctor(str(git_repo), write_log=False).command == "doctor"
    assert run_status(str(git_repo), write_log=False).command == "status"
    assert run_audit(str(git_repo), write_log=False).command == "audit"
    assert run_changed(str(git_repo), write_log=False).command == "changed"
    assert run_test_targeted(str(git_repo), write_log=False).command == "test"
    assert run_full_test(str(git_repo), write_log=False).command == "test"
    assert run_release_check(str(git_repo), write_log=False).command == "release-check"
    assert run_checkpoint(str(git_repo), write_log=False).command == "checkpoint"
    assert run_handoff(str(git_repo), write_log=False).command == "handoff"


def test_inspection_commands_including_process_list_remain_read_only(git_repo, monkeypatch):
    proc = ProcessInfo(pid=1, ppid=None, name="x", command_line=f"x --repo {git_repo}", working_directory=None, start_time_utc="2026-01-01T00:00:00Z")
    monkeypatch.setattr("forgeops.cli.process_list.list_os_processes", lambda: _discovery([proc]))

    before = _snapshot(git_repo)
    run_doctor(str(git_repo), write_log=False)
    run_status(str(git_repo), write_log=False)
    run_audit(str(git_repo), write_log=False)
    run_changed(str(git_repo), write_log=False)
    run_process_list(str(git_repo), write_log=False)
    after = _snapshot(git_repo)
    assert before == after


def test_cleanup_dry_run_is_read_only(git_repo, monkeypatch):
    record = RegistryRecord(
        pid=1, category="backend-dev-server", repository_root=str(git_repo),
        start_time_utc="2026-01-01T00:00:00Z", command_fingerprint="x", creation_source="test",
    )
    proc = ProcessInfo(pid=1, ppid=None, name="x", command_line="x", working_directory=None, start_time_utc="2026-01-01T00:00:00Z")
    monkeypatch.setattr("forgeops.cli.cleanup.list_os_processes", lambda: _discovery([proc]))
    monkeypatch.setattr("forgeops.cli.cleanup.load_registry", lambda repo: RegistryDocument(records=[record]))

    before = _snapshot(git_repo)
    run_cleanup(str(git_repo), write_log=False)  # dry-run (default)
    after = _snapshot(git_repo)
    assert before == after


def test_cleanup_only_ever_writes_the_documented_registry_path(git_repo, monkeypatch):
    record = RegistryRecord(
        pid=1, category="backend-dev-server", repository_root=str(git_repo),
        start_time_utc="2026-01-01T00:00:00Z", command_fingerprint="x", creation_source="test",
    )
    proc = ProcessInfo(pid=1, ppid=None, name="x", command_line="x", working_directory=None, start_time_utc="2026-01-01T00:00:00Z")
    monkeypatch.setattr("forgeops.cli.cleanup.list_os_processes", lambda: _discovery([proc]))
    monkeypatch.setattr("forgeops.cli.cleanup.load_registry", lambda repo: RegistryDocument(records=[record]))
    monkeypatch.setattr("forgeops.cli.cleanup.process_exists", lambda pid, timeout=None: False)  # "gone" -> stale removal path, no taskkill

    before = _snapshot(git_repo)
    run_cleanup(str(git_repo), write_log=False, execute=True)
    after = _snapshot(git_repo)

    changed = {k for k in after if before.get(k) != after.get(k)}
    assert changed <= {".agent/runtime/PROCESS_REGISTRY.json"}


def test_cleanup_never_receives_or_touches_a_different_repository_path(tmp_path, monkeypatch):
    """Even if process discovery reports a process whose command line
    mentions a completely different path (simulating e.g. a TrendForge
    reference), cleanup must only ever act against the repo_root it was
    explicitly invoked with - it never follows a path found in process
    metadata."""
    target_repo = tmp_path / "target"
    other_repo = tmp_path / "other-should-never-be-touched"
    for repo in (target_repo, other_repo):
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo), check=True)
        subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=str(repo), check=True)
        (repo / "f.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "add", "f.txt"], cwd=str(repo), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo), check=True)

    # A record with a `repository_root` pointing at `other_repo` shows up
    # in whatever registry `load_registry` returns for `target_repo` (a
    # stale/misplaced entry, however that might happen) - this must never
    # make cleanup, invoked against `target_repo`, write or delete
    # anything under `other_repo`. classify_process's repo-match check
    # must reject it (repository_root mismatch) regardless.
    record = RegistryRecord(
        pid=1, category="backend-dev-server", repository_root=str(other_repo),
        start_time_utc="2026-01-01T00:00:00Z", command_fingerprint="x", creation_source="test",
    )
    proc = ProcessInfo(pid=1, ppid=None, name="x", command_line=f"x --repo {other_repo}", working_directory=None, start_time_utc="2026-01-01T00:00:00Z")
    monkeypatch.setattr("forgeops.cli.cleanup.list_os_processes", lambda: _discovery([proc]))
    monkeypatch.setattr("forgeops.cli.cleanup.load_registry", lambda repo: RegistryDocument(records=[record]))

    before_other = _snapshot(other_repo)
    result = run_cleanup(str(target_repo), write_log=False, execute=True)
    after_other = _snapshot(other_repo)

    assert before_other == after_other
    assert not (other_repo / ".agent").exists()
    # The record (scoped to a different repo) is correctly ignored, not
    # acted on, from target_repo's perspective either.
    assert result.data["termination_candidates"] == []
