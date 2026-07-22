"""Structured result model shared by every ForgeOps CLI command: a list of
typed checks/findings plus a command-specific data payload, serializable
to stable JSON or rendered as concise human-readable text."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

STATUS_VALUES = ("pass", "warning", "blocked", "fail", "informational")


@dataclass(frozen=True)
class Check:
    id: str
    label: str
    status: str  # one of STATUS_VALUES
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in STATUS_VALUES:
            raise ValueError(f"invalid check status {self.status!r}, expected one of {STATUS_VALUES}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class CommandResult:
    command: str
    schema_version: int
    generated_at: str
    repo_root: str | None
    exit_code: int
    summary: str
    checks: list[Check] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "repo_root": self.repo_root,
            "exit_code": self.exit_code,
            "summary": self.summary,
            "checks": [c.to_dict() for c in self.checks],
            "data": self.data,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)


def counts_by_status(checks: list[Check]) -> dict[str, int]:
    counts = {status: 0 for status in STATUS_VALUES}
    for check in checks:
        counts[check.status] += 1
    return counts
