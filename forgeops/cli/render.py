"""Shared human-readable rendering helpers. Each command module builds its
own full render_human(result) using these primitives, since the data
payload shape differs per command."""
from __future__ import annotations

from forgeops.core.result import Check

_MARKERS = {
    "pass": "[ OK ]",
    "warning": "[WARN]",
    "blocked": "[BLOCK]",
    "fail": "[FAIL]",
    "informational": "[INFO]",
}


def marker(status: str) -> str:
    return _MARKERS.get(status, f"[{status.upper()}]")


def render_checks(checks: list[Check]) -> list[str]:
    return [f"{marker(c.status)} {c.label}: {c.message}" for c in checks]
