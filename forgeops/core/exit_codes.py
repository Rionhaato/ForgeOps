"""Stable, shared exit-code definitions. Every ForgeOps CLI command must
use these constants rather than ad-hoc integers. See docs/cli-exit-codes.md
for the authoritative documentation of what each code means."""
from __future__ import annotations

SUCCESS = 0
WARNINGS_PRESENT = 1
BLOCKED = 2
INVALID_CONFIG = 3
REPO_NOT_FOUND = 4
COMMAND_EXECUTION_FAILURE = 5
INTERNAL_ERROR = 6

DESCRIPTIONS: dict[int, str] = {
    SUCCESS: "Success - no warnings, no blocking findings.",
    WARNINGS_PRESENT: "Completed with one or more non-blocking warnings.",
    BLOCKED: "A blocking safety finding was reported (e.g. a likely secret).",
    INVALID_CONFIG: "ForgeOps configuration is present but invalid.",
    REPO_NOT_FOUND: "No git repository could be discovered at or above the given path.",
    COMMAND_EXECUTION_FAILURE: "A required external command (e.g. git) failed or was unavailable.",
    INTERNAL_ERROR: "An unexpected internal ForgeOps error occurred.",
}


def worst(*codes: int) -> int:
    """Return the highest-precedence (worst) exit code among those given,
    using the documented precedence order rather than plain integer max
    (INTERNAL_ERROR is defined as 6 but BLOCKED at 2 must still outrank
    WARNINGS_PRESENT at 1 - plain int ordering happens to match here, but
    this helper exists so the precedence is explicit and future-proof if
    codes are ever renumbered)."""
    order = [
        INTERNAL_ERROR,
        COMMAND_EXECUTION_FAILURE,
        REPO_NOT_FOUND,
        INVALID_CONFIG,
        BLOCKED,
        WARNINGS_PRESENT,
        SUCCESS,
    ]
    present = set(codes) or {SUCCESS}
    for code in order:
        if code in present:
            return code
    return SUCCESS
