"""Safe subprocess execution: no shell=True, explicit timeouts, and
graceful handling of a missing executable or a hung process instead of an
uncaught exception."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 15.0


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
    try:
        proc = subprocess.run(
            args,
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
