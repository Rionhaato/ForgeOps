"""Read-only OS process discovery for `forgeops process-list` /
`forgeops cleanup`. Windows-first (this toolkit's primary platform),
stdlib + OS-native tools only - never installs psutil or any other
package. Every raw command line is redacted before being stored on a
`ProcessInfo` (see `forgeops.security.redact`), since a command line can
legitimately contain a secret (e.g. `--token=...`) passed as an argument.

Windows limitation, documented rather than faked: WMI's `Win32_Process`
class exposes no native "current working directory" property (unlike
POSIX `/proc/<pid>/cwd`), so `working_directory` is always `None` on
Windows. Association therefore leans on command-line text and
parent/child relationships rather than CWD - see
`forgeops/detectors/process_association.py`."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from forgeops.core.subprocess_utils import DEFAULT_TIMEOUT_SECONDS, run
from forgeops.security.redact import redact_text

PS_TIMEOUT_SECONDS = 20.0
NETSTAT_TIMEOUT_SECONDS = 10.0

# WMI CIM_DATETIME, e.g. "20260722153045.123456-300" - the raw WMI string
# form, kept as a fallback parser.
_WMI_DATETIME_RE = re.compile(
    r"^(?P<y>\d{4})(?P<mo>\d{2})(?P<d>\d{2})(?P<h>\d{2})(?P<mi>\d{2})(?P<s>\d{2})\.(?P<us>\d{6})"
)
# What `Get-CimInstance ... | ConvertTo-Json` actually emits for a
# DateTime property in this environment: the legacy .NET JSON-date
# convention, milliseconds since the Unix epoch, e.g. "/Date(1784754813299)/".
# `Get-CimInstance` (unlike the older `Get-WmiObject`) auto-converts WMI's
# raw CIM_DATETIME string into a .NET DateTime, and PowerShell's
# ConvertTo-Json renders *that* this way - not as the raw WMI string this
# module's regex originally (incorrectly) assumed. This is the primary
# format actually encountered; the raw-string regex above is a fallback
# for any other CIM/WMI code path that might someday return it directly.
_DOTNET_JSON_DATE_RE = re.compile(r"^/Date\((?P<ms>-?\d+)\)/$")


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int | None
    name: str
    command_line: str | None  # already redact_text()-ed
    working_directory: str | None  # always None on Windows today (documented limitation)
    start_time_utc: str | None  # ISO 8601 UTC, or None if unavailable
    listening_ports: tuple[int, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ProcessDiscovery:
    processes: list[ProcessInfo]
    platform_supported: bool
    limitation: str | None  # non-None on partial or total enumeration failure


def _parse_wmi_datetime(raw: str | None) -> str | None:
    if not raw:
        return None

    dotnet_match = _DOTNET_JSON_DATE_RE.match(raw)
    if dotnet_match:
        try:
            dt = datetime.fromtimestamp(int(dotnet_match["ms"]) / 1000.0, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
        # Sub-second precision is dropped deliberately: WMI/CIM's own
        # timestamp resolution and .NET's epoch-millisecond round-trip
        # are both coarser than microseconds, and the PID-reuse identity
        # check only needs "the same process instance", not sub-second
        # precision - a second-granularity comparison is what's actually
        # reliable here.
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    wmi_match = _WMI_DATETIME_RE.match(raw)
    if wmi_match:
        try:
            dt = datetime(
                int(wmi_match["y"]), int(wmi_match["mo"]), int(wmi_match["d"]),
                int(wmi_match["h"]), int(wmi_match["mi"]), int(wmi_match["s"]),
                int(wmi_match["us"]), tzinfo=timezone.utc,
            )
        except ValueError:
            return None
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    return None


def _parse_netstat_listeners(netstat_output: str) -> dict[int, list[int]]:
    """PID -> list of ports it holds a LISTENING socket on. Only parses
    lines whose state column is exactly LISTENING - never touches or
    infers anything about established connections."""
    by_pid: dict[int, list[int]] = {}
    for line in netstat_output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] not in ("TCP", "TCP6"):
            continue
        if parts[3] != "LISTENING":
            continue
        local_addr = parts[1]
        pid_str = parts[4]
        if not pid_str.isdigit():
            continue
        port_str = local_addr.rsplit(":", 1)[-1]
        if not port_str.isdigit():
            continue
        by_pid.setdefault(int(pid_str), []).append(int(port_str))
    return by_pid


def _windows_processes(timeout: float) -> tuple[list[ProcessInfo], str | None]:
    ps_script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine,CreationDate | "
        "ConvertTo-Json -Compress"
    )
    proc = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script], timeout=timeout)
    if not proc.ok:
        detail = proc.error or f"powershell exited {proc.returncode}"
        return [], f"process enumeration via PowerShell/CIM failed: {detail}"

    try:
        parsed = json.loads(proc.stdout) if proc.stdout.strip() else []
    except json.JSONDecodeError as exc:
        return [], f"could not parse PowerShell/CIM process output: {exc}"

    # ConvertTo-Json emits a bare object (not a list) when exactly one
    # result matched - a well-known PowerShell JSON quirk.
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return [], "unexpected PowerShell/CIM process output shape"

    netstat_proc = run(["netstat.exe", "-ano"], timeout=NETSTAT_TIMEOUT_SECONDS)
    ports_by_pid = _parse_netstat_listeners(netstat_proc.stdout) if netstat_proc.ok else {}
    netstat_limitation = None if netstat_proc.ok else (
        f"listening-port discovery unavailable: {netstat_proc.error or 'netstat failed'}"
    )

    processes: list[ProcessInfo] = []
    for entry in parsed:
        if not isinstance(entry, dict) or "ProcessId" not in entry:
            continue
        pid = int(entry["ProcessId"])
        ppid_raw = entry.get("ParentProcessId")
        raw_cmdline = entry.get("CommandLine")
        processes.append(ProcessInfo(
            pid=pid,
            ppid=int(ppid_raw) if ppid_raw is not None else None,
            name=str(entry.get("Name") or ""),
            command_line=redact_text(raw_cmdline) if raw_cmdline else None,
            working_directory=None,  # not obtainable via Win32_Process - documented limitation
            start_time_utc=_parse_wmi_datetime(entry.get("CreationDate")),
            listening_ports=tuple(ports_by_pid.get(pid, ())),
        ))

    return processes, netstat_limitation


def list_os_processes(timeout: float = PS_TIMEOUT_SECONDS) -> ProcessDiscovery:
    """Enumerate OS processes. Never raises - any failure is reported via
    `limitation` with an empty (not partial-and-silently-wrong) process
    list, so callers never mistake 'could not determine' for 'nothing is
    running'."""
    if sys.platform != "win32":
        return ProcessDiscovery(
            processes=[],
            platform_supported=False,
            limitation=(
                f"process discovery is currently only implemented for Windows "
                f"(platform reported: {sys.platform}); no processes were enumerated"
            ),
        )

    processes, limitation = _windows_processes(timeout)
    return ProcessDiscovery(processes=processes, platform_supported=True, limitation=limitation)


def get_process_start_time_utc(pid: int, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str | None:
    """Single-process, cheap re-check used for PID-reuse revalidation
    immediately before any termination attempt - avoids re-enumerating
    every process on the system for a one-PID identity check."""
    if sys.platform != "win32":
        return None
    ps_script = (
        f"Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\" | "
        "Select-Object CreationDate | ConvertTo-Json -Compress"
    )
    proc = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script], timeout=timeout)
    if not proc.ok or not proc.stdout.strip():
        return None
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}
    if not isinstance(parsed, dict):
        return None
    return _parse_wmi_datetime(parsed.get("CreationDate"))


def process_exists(pid: int, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> bool:
    if sys.platform != "win32":
        return False
    ps_script = f"[bool](Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue)"
    proc = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script], timeout=timeout)
    return proc.ok and proc.stdout.strip().lower() == "true"
