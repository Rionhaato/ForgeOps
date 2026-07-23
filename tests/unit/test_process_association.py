"""Tests for forgeops/detectors/process_association.py: the pure,
safety-critical process<->repository classification logic. Every test
here is deterministic and I/O-free - no real process is ever touched."""
from __future__ import annotations

from pathlib import Path

from forgeops.detectors.process_association import classify_process, normalize_repo_path
from forgeops.detectors.processes import ProcessInfo
from forgeops.state.runtime_registry import RegistryDocument, RegistryRecord


def _proc(pid=100, ppid=1, name="python.exe", command_line="python -m something", start_time="2026-01-01T00:00:00Z"):
    return ProcessInfo(pid=pid, ppid=ppid, name=name, command_line=command_line, working_directory=None, start_time_utc=start_time)


def test_normalize_repo_path_handles_slashes_and_case():
    assert normalize_repo_path("C:\\Users\\Joshd\\ForgeOps") == normalize_repo_path("c:/users/joshd/forgeops/")


def test_no_evidence_is_unrelated(tmp_path):
    proc = _proc(command_line="some unrelated command")
    result = classify_process(proc, tmp_path, RegistryDocument(records=[]))
    assert result.classification == "unrelated"
    assert result.cleanup_eligible is False
    assert result.forgeops_managed is False


def test_unreadable_command_line_with_no_other_evidence_is_unrelated_not_uncertain(tmp_path):
    """A process whose command line simply could not be read (common for
    privileged system processes) and which has no other evidence must not
    be reported as 'uncertain' - that would make process-list noisy with
    every unreadable system process on the machine. Only genuinely
    conflicting/partial *registry* evidence should produce 'uncertain'."""
    proc = _proc(command_line=None)
    result = classify_process(proc, tmp_path, RegistryDocument(records=[]))
    assert result.classification == "unrelated"


def test_command_line_contains_repo_path_is_associated(tmp_path):
    proc = _proc(command_line=f"python -m server --repo {tmp_path}")
    result = classify_process(proc, tmp_path, RegistryDocument(records=[]))
    assert result.classification == "associated"
    assert result.cleanup_eligible is False
    assert result.forgeops_managed is False
    assert any("command line" in e for e in result.evidence)


def test_parent_command_line_contains_repo_path_is_associated(tmp_path):
    parent = _proc(pid=1, ppid=None, command_line=f"bash -c cd {tmp_path}")
    child = _proc(pid=100, ppid=1, command_line="some generic child command")
    result = classify_process(child, tmp_path, RegistryDocument(records=[]), all_by_pid={1: parent})
    assert result.classification == "associated"
    assert any("parent" in e for e in result.evidence)


def test_both_own_and_parent_evidence_gives_medium_confidence(tmp_path):
    parent = _proc(pid=1, ppid=None, command_line=f"bash -c cd {tmp_path}")
    child = _proc(pid=100, ppid=1, command_line=f"python -m server --repo {tmp_path}")
    result = classify_process(child, tmp_path, RegistryDocument(records=[]), all_by_pid={1: parent})
    assert result.classification == "associated"
    assert result.confidence == "medium"


def test_associated_is_never_cleanup_eligible(tmp_path):
    proc = _proc(command_line=f"python -m server --repo {tmp_path}")
    result = classify_process(proc, tmp_path, RegistryDocument(records=[]))
    assert result.cleanup_eligible is False
    assert result.ineligible_reason is not None


def test_common_executable_name_alone_is_never_associated(tmp_path):
    """python/node/npm/uvicorn/vite/git/powershell/cmd must never, by
    themselves, produce anything other than 'unrelated' - only explicit
    command-line/registry evidence counts."""
    for name in ("python", "node", "npm", "uvicorn", "vite", "git", "powershell", "cmd"):
        proc = _proc(name=name, command_line=f"{name} --some-unrelated-flag")
        result = classify_process(proc, tmp_path, RegistryDocument(records=[]))
        assert result.classification == "unrelated", f"{name} should not be associated by name alone"


def test_exact_registry_match_is_managed_and_eligible(tmp_path):
    proc = _proc(pid=500, start_time="2026-01-01T00:00:00Z")
    record = RegistryRecord(
        pid=500, category="backend-dev-server", repository_root=str(tmp_path),
        start_time_utc="2026-01-01T00:00:00Z", command_fingerprint="x", creation_source="test",
    )
    result = classify_process(proc, tmp_path, RegistryDocument(records=[record]))
    assert result.classification == "managed"
    assert result.forgeops_managed is True
    assert result.cleanup_eligible is True
    assert result.confidence == "high"


def test_registry_record_wrong_repository_is_uncertain_not_managed(tmp_path):
    proc = _proc(pid=500, start_time="2026-01-01T00:00:00Z")
    record = RegistryRecord(
        pid=500, category="backend-dev-server", repository_root="C:\\SomeOtherRepo",
        start_time_utc="2026-01-01T00:00:00Z", command_fingerprint="x", creation_source="test",
    )
    result = classify_process(proc, tmp_path, RegistryDocument(records=[record]))
    assert result.classification == "uncertain"
    assert result.cleanup_eligible is False


def test_registry_record_start_time_mismatch_is_stale_record_pid_reuse(tmp_path):
    """The core PID-reuse protection: same PID, same repo, but the live
    process's start time doesn't match what was recorded - the PID was
    reused by an unrelated process after the original exited."""
    proc = _proc(pid=500, start_time="2026-06-01T12:00:00Z")  # different from recorded
    record = RegistryRecord(
        pid=500, category="backend-dev-server", repository_root=str(tmp_path),
        start_time_utc="2026-01-01T00:00:00Z", command_fingerprint="x", creation_source="test",
    )
    result = classify_process(proc, tmp_path, RegistryDocument(records=[record]))
    assert result.classification == "stale_record"
    assert result.cleanup_eligible is False
    assert result.forgeops_managed is False
    assert "reuse" in (result.ineligible_reason or "").lower() or "reuse" in " ".join(result.evidence).lower()


def test_registry_record_missing_start_time_is_stale_record(tmp_path):
    proc = _proc(pid=500, start_time="2026-01-01T00:00:00Z")
    record = RegistryRecord(
        pid=500, category="backend-dev-server", repository_root=str(tmp_path),
        start_time_utc=None, command_fingerprint="x", creation_source="test",
    )
    result = classify_process(proc, tmp_path, RegistryDocument(records=[record]))
    assert result.classification == "stale_record"
    assert result.cleanup_eligible is False


def test_registry_record_disallowed_category_is_uncertain(tmp_path):
    proc = _proc(pid=500, start_time="2026-01-01T00:00:00Z")
    record = RegistryRecord(
        pid=500, category="editor", repository_root=str(tmp_path),
        start_time_utc="2026-01-01T00:00:00Z", command_fingerprint="x", creation_source="test",
    )
    result = classify_process(proc, tmp_path, RegistryDocument(records=[record]))
    assert result.classification == "uncertain"
    assert result.cleanup_eligible is False
    assert "editor" in (result.ineligible_reason or "")


def test_registry_record_never_managed_category_shell(tmp_path):
    proc = _proc(pid=500, start_time="2026-01-01T00:00:00Z")
    record = RegistryRecord(
        pid=500, category="shell", repository_root=str(tmp_path),
        start_time_utc="2026-01-01T00:00:00Z", command_fingerprint="x", creation_source="test",
    )
    result = classify_process(proc, tmp_path, RegistryDocument(records=[record]))
    assert result.classification == "uncertain"
    assert result.cleanup_eligible is False


def test_repository_path_with_spaces_matches_correctly(tmp_path):
    spacey = tmp_path / "a repo with spaces"
    spacey.mkdir()
    proc = _proc(command_line=f"python -m server --repo \"{spacey}\"")
    result = classify_process(proc, spacey, RegistryDocument(records=[]))
    assert result.classification == "associated"
