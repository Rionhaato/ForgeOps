"""Integration tests for `forgeops cleanup` (forgeops/cli/cleanup.py).
Everything here is mocked - no real process is ever spawned or
terminated by this test module (a real, disposable-process end-to-end
run was performed once manually during development; see
docs/process-list-and-cleanup.md and the Phase completion report for
that evidence). Never touches TrendForge or any real system process."""
from __future__ import annotations

import json

import pytest

from forgeops.cli.cleanup import run_cleanup, render_human
from forgeops.core import exit_codes
from forgeops.core.subprocess_utils import ProcResult
from forgeops.detectors.processes import ProcessDiscovery, ProcessInfo
from forgeops.state.runtime_registry import REGISTRY_RELATIVE_PATH, RegistryDocument, RegistryRecord


def _discovery(processes, limitation=None):
    return ProcessDiscovery(processes=processes, platform_supported=True, limitation=limitation)


def _proc(pid=500, ppid=1, name="python.exe", command_line="x", start_time="2026-01-01T00:00:00Z"):
    return ProcessInfo(pid=pid, ppid=ppid, name=name, command_line=command_line, working_directory=None, start_time_utc=start_time)


def _managed_record(pid=500, repo="C:\\repo", start_time="2026-01-01T00:00:00Z", category="backend-dev-server"):
    return RegistryRecord(pid=pid, category=category, repository_root=repo, start_time_utc=start_time, command_fingerprint="x", creation_source="test")


def _patch_common(monkeypatch, processes, registry, taskkill_ok=True, still_running_after=False):
    monkeypatch.setattr("forgeops.cli.cleanup.list_os_processes", lambda: _discovery(processes))
    monkeypatch.setattr("forgeops.cli.cleanup.load_registry", lambda repo: registry)

    call_log = {"taskkill_args": None, "save_registry_called": False}

    def fake_process_exists(pid, timeout=None):
        if call_log["taskkill_args"] is None:
            return True  # before termination attempted
        return still_running_after

    def fake_start_time(pid, timeout=None):
        for r in registry.records:
            if r.pid == pid:
                return r.start_time_utc
        return None

    def fake_run_subprocess(args, timeout=None):
        call_log["taskkill_args"] = args
        return ProcResult(args=tuple(args), returncode=0 if taskkill_ok else 1, stdout="", stderr="", timed_out=False, error=None if taskkill_ok else "access denied")

    def fake_save_registry(repo, doc):
        call_log["save_registry_called"] = True
        call_log["saved_records"] = doc.records

    monkeypatch.setattr("forgeops.cli.cleanup.process_exists", fake_process_exists)
    monkeypatch.setattr("forgeops.cli.cleanup.get_process_start_time_utc", fake_start_time)
    monkeypatch.setattr("forgeops.cli.cleanup.run_subprocess", fake_run_subprocess)
    monkeypatch.setattr("forgeops.cli.cleanup.save_registry", fake_save_registry)
    monkeypatch.setattr("forgeops.cli.cleanup.time.sleep", lambda s: None)
    return call_log


def test_dry_run_is_the_default(git_repo, monkeypatch):
    record = _managed_record(repo=str(git_repo))
    _patch_common(monkeypatch, [_proc()], RegistryDocument(records=[record]))
    result = run_cleanup(str(git_repo), write_log=False)  # execute defaults to False
    assert result.data["execute"] is False
    assert result.data["actions"][0]["terminated"] is False
    assert "dry-run" in result.data["actions"][0]["dry_run_outcome"]


def test_explicit_execute_required_for_real_action(git_repo, monkeypatch):
    record = _managed_record(repo=str(git_repo))
    call_log = _patch_common(monkeypatch, [_proc()], RegistryDocument(records=[record]), still_running_after=False)
    result = run_cleanup(str(git_repo), write_log=False, execute=True)
    assert result.data["execute"] is True
    assert call_log["taskkill_args"] is not None


def test_no_candidates(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.cleanup.list_os_processes", lambda: _discovery([]))
    monkeypatch.setattr("forgeops.cli.cleanup.load_registry", lambda repo: RegistryDocument(records=[]))
    result = run_cleanup(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["termination_candidates"] == []


def test_strongly_managed_disposable_process_dry_run(git_repo, monkeypatch):
    record = _managed_record(repo=str(git_repo))
    _patch_common(monkeypatch, [_proc()], RegistryDocument(records=[record]))
    result = run_cleanup(str(git_repo), write_log=False)
    assert len(result.data["termination_candidates"]) == 1
    assert result.data["termination_candidates"][0]["eligible"] is True


def test_weak_association_never_becomes_a_candidate(git_repo, monkeypatch):
    # A process with only heuristic command-line evidence (no registry
    # record) must never appear in termination_candidates at all.
    proc = _proc(command_line=f"python -m server --repo {git_repo}")
    monkeypatch.setattr("forgeops.cli.cleanup.list_os_processes", lambda: _discovery([proc]))
    monkeypatch.setattr("forgeops.cli.cleanup.load_registry", lambda repo: RegistryDocument(records=[]))
    result = run_cleanup(str(git_repo), write_log=False)
    assert result.data["termination_candidates"] == []


def test_stale_record_removed_safely_on_execute(git_repo, monkeypatch):
    # Registry has a record but the process no longer exists (not in discovery).
    record = _managed_record(pid=999, repo=str(git_repo))
    call_log = _patch_common(monkeypatch, [], RegistryDocument(records=[record]))
    result = run_cleanup(str(git_repo), write_log=False, execute=True)
    assert len(result.data["stale_record_candidates"]) == 1
    assert call_log["save_registry_called"] is True
    assert call_log["saved_records"] == []


def test_stale_record_only_reported_not_removed_on_dry_run(git_repo, monkeypatch):
    record = _managed_record(pid=999, repo=str(git_repo))
    call_log = _patch_common(monkeypatch, [], RegistryDocument(records=[record]))
    result = run_cleanup(str(git_repo), write_log=False)  # dry-run
    assert len(result.data["stale_record_candidates"]) == 1
    assert call_log["save_registry_called"] is False


def test_pid_reuse_rejected_never_a_termination_candidate(git_repo, monkeypatch):
    proc = _proc(pid=500, start_time="2026-07-01T00:00:00Z")  # different from registry
    record = _managed_record(pid=500, repo=str(git_repo), start_time="2026-01-01T00:00:00Z")
    monkeypatch.setattr("forgeops.cli.cleanup.list_os_processes", lambda: _discovery([proc]))
    monkeypatch.setattr("forgeops.cli.cleanup.load_registry", lambda repo: RegistryDocument(records=[record]))
    result = run_cleanup(str(git_repo), write_log=False, execute=True)
    assert result.data["termination_candidates"] == []
    # Reported as a stale record (PID reuse), not silently dropped.
    assert len(result.data["stale_record_candidates"]) == 1


def test_graceful_termination_success(git_repo, monkeypatch):
    record = _managed_record(repo=str(git_repo))
    call_log = _patch_common(monkeypatch, [_proc()], RegistryDocument(records=[record]), taskkill_ok=True, still_running_after=False)
    result = run_cleanup(str(git_repo), write_log=False, execute=True)
    assert result.data["actions"][0]["terminated"] is True
    assert "gracefully terminated" in result.data["actions"][0]["dry_run_outcome"]
    assert call_log["save_registry_called"] is True
    assert call_log["saved_records"] == []


def test_graceful_termination_timeout_does_not_escalate(git_repo, monkeypatch):
    record = _managed_record(repo=str(git_repo))
    call_log = _patch_common(monkeypatch, [_proc()], RegistryDocument(records=[record]), taskkill_ok=True, still_running_after=True)
    result = run_cleanup(str(git_repo), write_log=False, execute=True, graceful_wait_seconds=0.01)
    assert result.data["actions"][0]["terminated"] is False
    assert "did not stop" in result.data["actions"][0]["dry_run_outcome"]
    # The record must still be present - nothing was actually cleaned up.
    assert call_log["taskkill_args"] is not None
    assert "/F" not in call_log["taskkill_args"]
    assert "-Force" not in call_log["taskkill_args"]


def test_inaccessible_process_at_revalidation_is_skipped_safely(git_repo, monkeypatch):
    record = _managed_record(repo=str(git_repo))
    monkeypatch.setattr("forgeops.cli.cleanup.list_os_processes", lambda: _discovery([_proc()]))
    monkeypatch.setattr("forgeops.cli.cleanup.load_registry", lambda repo: RegistryDocument(records=[record]))
    monkeypatch.setattr("forgeops.cli.cleanup.process_exists", lambda pid, timeout=None: False)  # gone by the time we revalidate
    called_taskkill = {"called": False}

    def fake_run_subprocess(args, timeout=None):
        called_taskkill["called"] = True
        return ProcResult(args=tuple(args), returncode=0, stdout="", stderr="", timed_out=False, error=None)

    monkeypatch.setattr("forgeops.cli.cleanup.run_subprocess", fake_run_subprocess)
    monkeypatch.setattr("forgeops.cli.cleanup.save_registry", lambda repo, doc: None)
    result = run_cleanup(str(git_repo), write_log=False, execute=True)
    assert called_taskkill["called"] is False
    assert "no longer exists" in result.data["actions"][0]["dry_run_outcome"]


def test_already_exited_process_reported_as_stale_not_error(git_repo, monkeypatch):
    record = _managed_record(pid=12345, repo=str(git_repo))
    _patch_common(monkeypatch, [], RegistryDocument(records=[record]))  # not in live process list at all
    result = run_cleanup(str(git_repo), write_log=False)
    assert result.exit_code != exit_codes.INTERNAL_ERROR
    assert len(result.data["stale_record_candidates"]) == 1


def test_repository_path_with_spaces(tmp_path, monkeypatch):
    import subprocess
    spacey = tmp_path / "a repo with spaces"
    spacey.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(spacey), check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(spacey), check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=str(spacey), check=True)
    (spacey / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=str(spacey), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(spacey), check=True)

    record = _managed_record(repo=str(spacey))
    _patch_common(monkeypatch, [_proc()], RegistryDocument(records=[record]))
    result = run_cleanup(str(spacey), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT  # candidate present
    assert len(result.data["termination_candidates"]) == 1


def test_json_output(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.cleanup.list_os_processes", lambda: _discovery([]))
    monkeypatch.setattr("forgeops.cli.cleanup.load_registry", lambda repo: RegistryDocument(records=[]))
    result = run_cleanup(str(git_repo), write_log=False)
    payload = json.loads(result.to_json())
    assert payload["command"] == "cleanup"


def test_human_output(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.cleanup.list_os_processes", lambda: _discovery([]))
    monkeypatch.setattr("forgeops.cli.cleanup.load_registry", lambda repo: RegistryDocument(records=[]))
    result = run_cleanup(str(git_repo), write_log=False)
    assert "forgeops cleanup" in render_human(result)


def test_secret_sanitization(git_repo, monkeypatch):
    proc = _proc(command_line=f"python -m server --repo {git_repo} --token Bearer abcdefghijklmnop1234")  # forgeops:allow-secret
    monkeypatch.setattr("forgeops.cli.cleanup.list_os_processes", lambda: _discovery([proc]))
    monkeypatch.setattr("forgeops.cli.cleanup.load_registry", lambda repo: RegistryDocument(records=[]))
    result = run_cleanup(str(git_repo), write_log=False)
    assert "abcdefghijklmnop1234" not in result.to_json()


def test_atomic_runtime_record_handling_on_termination(git_repo, monkeypatch):
    record_a = _managed_record(pid=500, repo=str(git_repo))
    record_b = _managed_record(pid=501, repo=str(git_repo))
    call_log = _patch_common(
        monkeypatch, [_proc(pid=500), _proc(pid=501)],
        RegistryDocument(records=[record_a, record_b]),
        taskkill_ok=True, still_running_after=False,
    )
    result = run_cleanup(str(git_repo), write_log=False, execute=True)
    # Both terminated -> registry saved once with both records removed, not partially.
    assert call_log["save_registry_called"] is True
    assert call_log["saved_records"] == []


def test_no_deletion_outside_runtime_paths(git_repo, monkeypatch):
    record = _managed_record(repo=str(git_repo))
    _patch_common(monkeypatch, [_proc()], RegistryDocument(records=[record]), still_running_after=False)

    def snapshot():
        return {p.relative_to(git_repo).as_posix() for p in git_repo.rglob("*") if p.is_file() and "logs" not in p.parts and ".git" not in p.parts}

    before = snapshot()
    run_cleanup(str(git_repo), write_log=False, execute=True)
    after = snapshot()
    # save_registry is mocked (doesn't really write here), so nothing on
    # disk should have changed at all under this test's mocking.
    assert before == after


def test_no_termination_of_unknown_processes(git_repo, monkeypatch):
    # A process that's simply running, unrelated, with no registry record
    # at all - must never be touched, regardless of execute mode.
    unrelated = _proc(pid=42, command_line="notepad.exe some_unrelated_file.txt")
    monkeypatch.setattr("forgeops.cli.cleanup.list_os_processes", lambda: _discovery([unrelated]))
    monkeypatch.setattr("forgeops.cli.cleanup.load_registry", lambda repo: RegistryDocument(records=[]))
    called = {"taskkill": False}

    def fake_run_subprocess(args, timeout=None):
        called["taskkill"] = True
        return ProcResult(args=tuple(args), returncode=0, stdout="", stderr="", timed_out=False, error=None)

    monkeypatch.setattr("forgeops.cli.cleanup.run_subprocess", fake_run_subprocess)
    result = run_cleanup(str(git_repo), write_log=False, execute=True)
    assert called["taskkill"] is False
    assert result.data["termination_candidates"] == []


def test_no_force_kill_flag_ever_used(git_repo, monkeypatch):
    record = _managed_record(repo=str(git_repo))
    call_log = _patch_common(monkeypatch, [_proc()], RegistryDocument(records=[record]), still_running_after=False)
    run_cleanup(str(git_repo), write_log=False, execute=True)
    assert call_log["taskkill_args"] is not None
    assert "/F" not in call_log["taskkill_args"]
    assert "-Force" not in call_log["taskkill_args"]
    assert "/f" not in [a.lower() if isinstance(a, str) else a for a in call_log["taskkill_args"]]


def test_dry_run_flag_explicit_is_equivalent_to_default(git_repo, monkeypatch):
    record = _managed_record(repo=str(git_repo))
    _patch_common(monkeypatch, [_proc()], RegistryDocument(records=[record]))
    result_default = run_cleanup(str(git_repo), write_log=False)
    result_explicit = run_cleanup(str(git_repo), write_log=False, execute=False)
    assert result_default.data["execute"] == result_explicit.data["execute"] == False


def test_repo_not_found(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    result = run_cleanup(str(outside), write_log=False)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND


def test_git_unavailable(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.cleanup.git_version", lambda: None)
    result = run_cleanup(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE
