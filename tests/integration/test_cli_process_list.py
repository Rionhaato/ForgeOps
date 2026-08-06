"""Integration tests for `forgeops process-list` (forgeops/cli/process_list.py).
Uses mocks for OS-level process discovery throughout - never depends on
real system process state, matching the "no matching / strongly
associated / weak match / stale record / PID reuse / ..." scenarios."""
from __future__ import annotations

import json

from forgeops.cli.process_list import render_human, run_process_list
from forgeops.core import exit_codes
from forgeops.detectors.processes import ProcessDiscovery, ProcessInfo
from forgeops.state.runtime_registry import RegistryDocument, RegistryRecord


def _discovery(processes, limitation=None, platform_supported=True):
    return ProcessDiscovery(processes=processes, platform_supported=platform_supported, limitation=limitation)


def _proc(pid=100, ppid=1, name="python.exe", command_line="x", start_time="2026-01-01T00:00:00Z", ports=()):
    return ProcessInfo(pid=pid, ppid=ppid, name=name, command_line=command_line, working_directory=None, start_time_utc=start_time, listening_ports=tuple(ports))


def test_no_matching_processes(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.process_list.list_os_processes", lambda: _discovery([_proc(command_line="totally unrelated")]))
    result = run_process_list(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS
    assert result.data["processes"] == []
    assert result.data["total_processes_scanned"] == 1


def test_strongly_associated_managed_process(git_repo, monkeypatch):
    proc = _proc(pid=500, start_time="2026-01-01T00:00:00Z", ports=(8000,))
    record = RegistryRecord(
        pid=500, category="backend-dev-server", repository_root=str(git_repo),
        start_time_utc="2026-01-01T00:00:00Z", command_fingerprint="x", creation_source="test",
    )
    monkeypatch.setattr("forgeops.cli.process_list.list_os_processes", lambda: _discovery([proc]))
    monkeypatch.setattr("forgeops.cli.process_list.load_registry", lambda repo: RegistryDocument(records=[record]))
    result = run_process_list(str(git_repo), write_log=False)
    assert len(result.data["processes"]) == 1
    assert result.data["processes"][0]["classification"] == "managed"
    assert result.data["processes"][0]["cleanup_eligible"] is True
    assert result.data["processes"][0]["listening_ports"] == [8000]


def test_process_with_repository_path_containing_spaces(tmp_path, monkeypatch):
    import subprocess
    spacey = tmp_path / "a repo with spaces"
    spacey.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(spacey), check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(spacey), check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=str(spacey), check=True)
    (spacey / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=str(spacey), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(spacey), check=True)

    proc = _proc(command_line=f"python -m server --repo \"{spacey}\"")
    monkeypatch.setattr("forgeops.cli.process_list.list_os_processes", lambda: _discovery([proc]))
    result = run_process_list(str(spacey), write_log=False)
    assert result.data["processes"][0]["classification"] == "associated"


def test_weak_executable_name_only_match_remains_ineligible(git_repo, monkeypatch):
    proc = _proc(name="python", command_line="python --unrelated-flag")
    monkeypatch.setattr("forgeops.cli.process_list.list_os_processes", lambda: _discovery([proc]))
    result = run_process_list(str(git_repo), write_log=False)
    assert result.data["processes"] == []  # unrelated -> not reported at all


def test_sanitized_command_output(git_repo, monkeypatch):
    proc = _proc(command_line=f"python -m server --repo {git_repo} --token Bearer abcdefghijklmnop1234")  # forgeops:allow-secret
    monkeypatch.setattr("forgeops.cli.process_list.list_os_processes", lambda: _discovery([proc]))
    result = run_process_list(str(git_repo), write_log=False)
    assert "abcdefghijklmnop1234" not in result.to_json()
    assert "abcdefghijklmnop1234" not in render_human(result)


def test_missing_optional_metadata_handled_gracefully(git_repo, monkeypatch):
    proc = ProcessInfo(pid=1, ppid=None, name="x", command_line=None, working_directory=None, start_time_utc=None)
    monkeypatch.setattr("forgeops.cli.process_list.list_os_processes", lambda: _discovery([proc]))
    result = run_process_list(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.SUCCESS  # unrelated, not an error


def test_inaccessible_process_metadata_reported_as_limitation(git_repo, monkeypatch):
    monkeypatch.setattr(
        "forgeops.cli.process_list.list_os_processes",
        lambda: _discovery([], limitation="process enumeration via PowerShell/CIM failed: access denied"),
    )
    result = run_process_list(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    ids = {c.id: c for c in result.checks}
    assert ids["process-discovery-limitation"].status == "warning"


def test_stale_pid_record_is_reported(git_repo, monkeypatch):
    proc = _proc(pid=500, start_time="2026-06-01T00:00:00Z")  # process still exists but process-list has no live match check here
    record = RegistryRecord(
        pid=500, category="backend-dev-server", repository_root=str(git_repo),
        start_time_utc="2026-01-01T00:00:00Z",  # different from proc's actual start time -> stale
        command_fingerprint="x", creation_source="test",
    )
    monkeypatch.setattr("forgeops.cli.process_list.list_os_processes", lambda: _discovery([proc]))
    monkeypatch.setattr("forgeops.cli.process_list.load_registry", lambda repo: RegistryDocument(records=[record]))
    result = run_process_list(str(git_repo), write_log=False)
    assert result.data["processes"][0]["classification"] == "stale_record"
    assert result.data["processes"][0]["cleanup_eligible"] is False


def test_pid_reuse_mismatch_reported_as_stale_record(git_repo, monkeypatch):
    # Same scenario as above, phrased explicitly as the PID-reuse case.
    proc = _proc(pid=777, start_time="2026-07-01T09:00:00Z")
    record = RegistryRecord(
        pid=777, category="test-runner", repository_root=str(git_repo),
        start_time_utc="2026-01-01T00:00:00Z", command_fingerprint="x", creation_source="test",
    )
    monkeypatch.setattr("forgeops.cli.process_list.list_os_processes", lambda: _discovery([proc]))
    monkeypatch.setattr("forgeops.cli.process_list.load_registry", lambda repo: RegistryDocument(records=[record]))
    result = run_process_list(str(git_repo), write_log=False)
    assert result.data["processes"][0]["classification"] == "stale_record"
    assert result.data["processes"][0]["forgeops_managed"] is False


def test_human_output(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.process_list.list_os_processes", lambda: _discovery([]))
    result = run_process_list(str(git_repo), write_log=False)
    rendered = render_human(result)
    assert "forgeops process-list" in rendered


def test_json_output(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.process_list.list_os_processes", lambda: _discovery([]))
    result = run_process_list(str(git_repo), write_log=False)
    payload = json.loads(result.to_json())
    assert payload["command"] == "process-list"


def test_raw_log_written_when_enabled(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.process_list.list_os_processes", lambda: _discovery([]))
    run_process_list(str(git_repo), write_log=True)
    logs = list((git_repo / "logs" / "process-list").glob("*/process-list.log"))
    assert len(logs) == 1


def test_unsupported_platform_behavior(git_repo, monkeypatch):
    monkeypatch.setattr(
        "forgeops.cli.process_list.list_os_processes",
        lambda: _discovery([], platform_supported=False, limitation="process discovery is currently only implemented for Windows"),
    )
    result = run_process_list(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.WARNINGS_PRESENT
    assert result.data["platform_supported"] is False


def test_repo_not_found(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    result = run_process_list(str(outside), write_log=False)
    assert result.exit_code == exit_codes.REPO_NOT_FOUND


def test_git_unavailable(git_repo, monkeypatch):
    monkeypatch.setattr("forgeops.cli.process_list.git_version", lambda: None)
    result = run_process_list(str(git_repo), write_log=False)
    assert result.exit_code == exit_codes.COMMAND_EXECUTION_FAILURE


def test_read_only_never_writes_outside_logs(git_repo, monkeypatch):
    proc = _proc(command_line=f"python -m server --repo {git_repo}")
    monkeypatch.setattr("forgeops.cli.process_list.list_os_processes", lambda: _discovery([proc]))

    def snapshot():
        return {p.relative_to(git_repo).as_posix(): p.stat().st_mtime for p in git_repo.rglob("*") if p.is_file() and "logs" not in p.parts and ".git" not in p.parts}

    before = snapshot()
    run_process_list(str(git_repo), write_log=False)
    after = snapshot()
    assert before == after
