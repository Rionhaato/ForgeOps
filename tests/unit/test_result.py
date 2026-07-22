from __future__ import annotations

import json

import pytest

from forgeops.core import exit_codes
from forgeops.core.result import Check, CommandResult, counts_by_status


def test_check_rejects_invalid_status():
    with pytest.raises(ValueError):
        Check("id", "label", "not-a-real-status", "message")


def test_command_result_to_json_is_valid_json():
    result = CommandResult(
        command="doctor", schema_version=1, generated_at="2026-01-01T00:00:00Z",
        repo_root="/tmp/x", exit_code=0, summary="ok",
        checks=[Check("a", "A", "pass", "fine")],
    )
    parsed = json.loads(result.to_json())
    assert parsed["command"] == "doctor"
    assert parsed["checks"][0]["id"] == "a"


def test_command_result_json_is_stable_across_calls():
    result = CommandResult(
        command="status", schema_version=1, generated_at="t", repo_root=None,
        exit_code=0, summary="s", checks=[Check("a", "A", "pass", "m")],
    )
    assert result.to_json() == result.to_json()


def test_counts_by_status():
    checks = [Check("a", "A", "pass", "m"), Check("b", "B", "warning", "m"), Check("c", "C", "pass", "m")]
    counts = counts_by_status(checks)
    assert counts["pass"] == 2
    assert counts["warning"] == 1
    assert counts["blocked"] == 0


def test_exit_code_worst_precedence():
    assert exit_codes.worst(exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT) == exit_codes.WARNINGS_PRESENT
    assert exit_codes.worst(exit_codes.WARNINGS_PRESENT, exit_codes.BLOCKED) == exit_codes.BLOCKED
    assert exit_codes.worst(exit_codes.BLOCKED, exit_codes.INTERNAL_ERROR) == exit_codes.INTERNAL_ERROR
    assert exit_codes.worst() == exit_codes.SUCCESS
