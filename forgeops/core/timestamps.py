"""Deterministic timestamp helpers. Every caller accepts an optional
`clock` callable (no-arg, returns a UTC datetime) so tests can inject a
fixed time instead of asserting against wall-clock output."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

Clock = Callable[[], datetime]


def _now(clock: Clock | None) -> datetime:
    dt = clock() if clock is not None else datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def iso_now(clock: Clock | None = None) -> str:
    """RFC3339/ISO8601 UTC timestamp, e.g. 2026-07-21T22:15:00Z."""
    return _now(clock).strftime("%Y-%m-%dT%H:%M:%SZ")


def path_timestamp(clock: Clock | None = None) -> str:
    """Filesystem-safe timestamp for log directory names, e.g. 20260721-221500."""
    return _now(clock).strftime("%Y%m%d-%H%M%S")
