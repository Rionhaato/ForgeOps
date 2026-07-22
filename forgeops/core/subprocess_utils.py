"""Safe subprocess execution: no shell=True, explicit timeouts, and
graceful handling of a missing executable or a hung process instead of an
uncaught exception."""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 15.0


def _resolve_executable(name: str) -> str:
    """On Windows, tools like npm/npx/pnpm/yarn are installed as .cmd
    shims, not .exe files. subprocess.run([...], shell=False) - which
    this module always uses, deliberately, to avoid shell-injection risk
    - does not perform the PATHEXT resolution a real shell would, so
    "npm" alone raises FileNotFoundError even when npm is genuinely on
    PATH. shutil.which() does perform that resolution. Resolving here
    keeps shell=False everywhere while still finding these shims; on
    non-Windows platforms this is a no-op passthrough (which() still
    finds plain executables, and if it doesn't, the original name is
    passed through unchanged so the FileNotFoundError path still fires
    normally)."""
    if sys.platform != "win32":
        return name
    resolved = shutil.which(name)
    return resolved if resolved else name


@dataclass(frozen=True)
class ProcResult:
    args: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out and self.returncode == 0


def run(
    args: list[str],
    cwd: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ProcResult:
    """Run args as a subprocess. Never raises: a missing executable or a
    timeout is reported in the returned ProcResult, not as an exception."""
    resolved_args = [_resolve_executable(args[0]), *args[1:]] if args else args
    try:
        proc = subprocess.run(
            resolved_args,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        return ProcResult(
            args=tuple(args),
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            timed_out=False,
            error=None,
        )
    except FileNotFoundError:
        return ProcResult(
            args=tuple(args),
            returncode=None,
            stdout="",
            stderr="",
            timed_out=False,
            error=f"executable not found: {args[0]}",
        )
    except subprocess.TimeoutExpired:
        return ProcResult(
            args=tuple(args),
            returncode=None,
            stdout="",
            stderr="",
            timed_out=True,
            error=f"timed out after {timeout}s",
        )
    except OSError as exc:
        return ProcResult(
            args=tuple(args),
            returncode=None,
            stdout="",
            stderr="",
            timed_out=False,
            error=f"failed to execute: {exc}",
        )
