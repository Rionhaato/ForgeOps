"""Raw output persistence under logs/<command>/<timestamp>/. Every write
goes through redact_text first - nothing reaches disk unredacted, even
though the commands calling this should already avoid capturing secret
values in the first place (defense in depth)."""
from __future__ import annotations

from pathlib import Path

from forgeops.core.timestamps import Clock, path_timestamp
from forgeops.security.redact import redact_text


class LogWriter:
    def __init__(
        self,
        repo_root: Path,
        command: str,
        log_dir_name: str = "logs",
        clock: Clock | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.command = command
        self.run_dir = repo_root / log_dir_name / command / path_timestamp(clock)

    def write(self, filename: str, content: str) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        redacted = redact_text(content)
        path = self.run_dir / filename
        path.write_text(redacted, encoding="utf-8")
        return path
