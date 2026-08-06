"""Tests for forgeops/detectors/processes.py: WMI/CIM datetime parsing,
netstat listener parsing, and platform-limitation reporting."""
from __future__ import annotations

from forgeops.detectors.processes import (
    ProcessDiscovery,
    _parse_netstat_listeners,
    _parse_wmi_datetime,
    list_os_processes,
)


def test_parses_dotnet_json_date_format():
    # What Get-CimInstance's ConvertTo-Json actually emits for a DateTime
    # property in this environment - the primary format encountered.
    result = _parse_wmi_datetime("/Date(1784754813299)/")
    assert result == "2026-07-22T21:13:33Z"


def test_parses_raw_wmi_cim_datetime_format():
    # Fallback format, in case a different CIM/WMI code path ever returns it.
    result = _parse_wmi_datetime("20260722153045.123456-300")
    assert result == "2026-07-22T15:30:45Z"


def test_parses_none_as_none():
    assert _parse_wmi_datetime(None) is None


def test_parses_empty_string_as_none():
    assert _parse_wmi_datetime("") is None


def test_parses_garbage_as_none():
    assert _parse_wmi_datetime("not a date at all") is None


def test_netstat_listeners_only_counts_listening_state():
    output = (
        "  Proto  Local Address          Foreign Address        State           PID\n"
        "  TCP    127.0.0.1:5174         0.0.0.0:0              LISTENING       1234\n"
        "  TCP    127.0.0.1:54321        127.0.0.1:8000         ESTABLISHED     5678\n"
        "  TCP6   [::]:8000              [::]:0                 LISTENING       9999\n"
    )
    result = _parse_netstat_listeners(output)
    assert result == {1234: [5174], 9999: [8000]}


def test_netstat_listeners_empty_output():
    assert _parse_netstat_listeners("") == {}


def test_netstat_listeners_malformed_lines_are_skipped():
    output = "garbage line with too few fields\nTCP incomplete\n"
    assert _parse_netstat_listeners(output) == {}


def test_non_windows_platform_reports_limitation(monkeypatch):
    monkeypatch.setattr("forgeops.detectors.processes.sys.platform", "linux")
    discovery = list_os_processes()
    assert isinstance(discovery, ProcessDiscovery)
    assert discovery.platform_supported is False
    assert discovery.processes == []
    assert discovery.limitation is not None
    assert "Windows" in discovery.limitation


def test_powershell_failure_reports_limitation_not_a_crash(monkeypatch):
    monkeypatch.setattr("forgeops.detectors.processes.sys.platform", "win32")

    def fake_run(args, timeout=None):
        from forgeops.core.subprocess_utils import ProcResult
        return ProcResult(args=tuple(args), returncode=None, stdout="", stderr="", timed_out=False, error="executable not found: powershell.exe")

    monkeypatch.setattr("forgeops.detectors.processes.run", fake_run)
    discovery = list_os_processes()
    assert discovery.processes == []
    assert discovery.limitation is not None
    assert "PowerShell" in discovery.limitation or "powershell" in discovery.limitation.lower()


def test_malformed_json_output_reports_limitation_not_a_crash(monkeypatch):
    monkeypatch.setattr("forgeops.detectors.processes.sys.platform", "win32")

    def fake_run(args, timeout=None):
        from forgeops.core.subprocess_utils import ProcResult
        if "netstat.exe" in args[0]:
            return ProcResult(args=tuple(args), returncode=0, stdout="", stderr="", timed_out=False, error=None)
        return ProcResult(args=tuple(args), returncode=0, stdout="{ not valid json", stderr="", timed_out=False, error=None)

    monkeypatch.setattr("forgeops.detectors.processes.run", fake_run)
    discovery = list_os_processes()
    assert discovery.processes == []
    assert discovery.limitation is not None


def test_single_process_result_bare_object_is_handled(monkeypatch):
    """ConvertTo-Json emits a bare object, not a list, when exactly one
    result matches - must not be silently dropped."""
    monkeypatch.setattr("forgeops.detectors.processes.sys.platform", "win32")

    def fake_run(args, timeout=None):
        from forgeops.core.subprocess_utils import ProcResult
        if "netstat.exe" in args[0]:
            return ProcResult(args=tuple(args), returncode=0, stdout="", stderr="", timed_out=False, error=None)
        payload = '{"ProcessId":42,"ParentProcessId":1,"Name":"test.exe","CommandLine":"test.exe --flag","CreationDate":"/Date(1784754813299)/"}'
        return ProcResult(args=tuple(args), returncode=0, stdout=payload, stderr="", timed_out=False, error=None)

    monkeypatch.setattr("forgeops.detectors.processes.run", fake_run)
    discovery = list_os_processes()
    assert len(discovery.processes) == 1
    assert discovery.processes[0].pid == 42
    assert discovery.processes[0].start_time_utc == "2026-07-22T21:13:33Z"


def test_command_line_is_redacted(monkeypatch):
    monkeypatch.setattr("forgeops.detectors.processes.sys.platform", "win32")

    def fake_run(args, timeout=None):
        from forgeops.core.subprocess_utils import ProcResult
        if "netstat.exe" in args[0]:
            return ProcResult(args=tuple(args), returncode=0, stdout="", stderr="", timed_out=False, error=None)
        payload = (
            '[{"ProcessId":42,"ParentProcessId":1,"Name":"test.exe",'
            '"CommandLine":"test.exe --token Bearer abcdefghij1234567890",'  # forgeops:allow-secret
            '"CreationDate":"/Date(1784754813299)/"}]'
        )
        return ProcResult(args=tuple(args), returncode=0, stdout=payload, stderr="", timed_out=False, error=None)

    monkeypatch.setattr("forgeops.detectors.processes.run", fake_run)
    discovery = list_os_processes()
    assert "abcdefghij1234567890" not in (discovery.processes[0].command_line or "")


def test_working_directory_is_always_none_on_windows(monkeypatch):
    monkeypatch.setattr("forgeops.detectors.processes.sys.platform", "win32")

    def fake_run(args, timeout=None):
        from forgeops.core.subprocess_utils import ProcResult
        if "netstat.exe" in args[0]:
            return ProcResult(args=tuple(args), returncode=0, stdout="", stderr="", timed_out=False, error=None)
        payload = '[{"ProcessId":42,"ParentProcessId":1,"Name":"test.exe","CommandLine":"x","CreationDate":null}]'
        return ProcResult(args=tuple(args), returncode=0, stdout=payload, stderr="", timed_out=False, error=None)

    monkeypatch.setattr("forgeops.detectors.processes.run", fake_run)
    discovery = list_os_processes()
    assert discovery.processes[0].working_directory is None
