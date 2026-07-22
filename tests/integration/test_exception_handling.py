"""Tests for the top-level exception boundary in forgeops/cli/__init__.py
(Phase 2B, Part 5): an unexpected internal error must never produce a raw
traceback by default, must return exit code 6 (INTERNAL_ERROR), must
write a redacted diagnostic log, and must still allow a raw traceback
through an explicit --debug flag or FORGEOPS_DEBUG env var. Known/
expected errors (missing repo, invalid config) must keep their own exit
codes rather than being swallowed into INTERNAL_ERROR."""
from __future__ import annotations

import json

import pytest

from forgeops.cli import main
from forgeops.core import exit_codes


def test_unexpected_exception_in_human_mode_returns_internal_error(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug")

    monkeypatch.setattr("forgeops.cli.doctor_cmd.run_doctor", boom)
    exit_code = main(["doctor", "--repo", str(git_repo)])
    captured = capsys.readouterr()

    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug" in captured.err
    assert "diagnostic log" in captured.err


def test_unexpected_exception_in_json_mode_returns_structured_error(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug")

    monkeypatch.setattr("forgeops.cli.doctor_cmd.run_doctor", boom)
    exit_code = main(["doctor", "--repo", str(git_repo), "--json"])
    captured = capsys.readouterr()

    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"
    assert "simulated internal bug" in payload["message"]
    assert "Traceback" not in captured.out


def test_diagnostic_log_is_written(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug")

    monkeypatch.setattr("forgeops.cli.doctor_cmd.run_doctor", boom)
    main(["doctor", "--repo", str(git_repo)])
    log_files = list((git_repo / "logs" / "doctor").glob("*/internal_error.log"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert "RuntimeError" in content
    assert "simulated internal bug" in content


def test_diagnostic_log_is_redacted(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("leaked postgres://user:hunter2@host/db in the error")  # forgeops:allow-secret

    monkeypatch.setattr("forgeops.cli.doctor_cmd.run_doctor", boom)
    main(["doctor", "--repo", str(git_repo)])
    log_files = list((git_repo / "logs" / "doctor").glob("*/internal_error.log"))
    content = log_files[0].read_text(encoding="utf-8")
    assert "hunter2" not in content
    assert "[REDACTED_DB_URL]" in content


def test_error_message_on_stderr_is_redacted(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("leaked postgres://user:hunter2@host/db in the error")  # forgeops:allow-secret

    monkeypatch.setattr("forgeops.cli.doctor_cmd.run_doctor", boom)
    main(["doctor", "--repo", str(git_repo)])
    captured = capsys.readouterr()
    assert "hunter2" not in captured.err


def test_debug_flag_lets_the_real_exception_propagate(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug")

    monkeypatch.setattr("forgeops.cli.doctor_cmd.run_doctor", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug"):
        main(["--debug", "doctor", "--repo", str(git_repo)])


def test_debug_env_var_lets_the_real_exception_propagate(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug")

    monkeypatch.setattr("forgeops.cli.doctor_cmd.run_doctor", boom)
    monkeypatch.setenv("FORGEOPS_DEBUG", "1")
    with pytest.raises(RuntimeError, match="simulated internal bug"):
        main(["doctor", "--repo", str(git_repo)])


def test_debug_env_var_false_values_do_not_enable_debug(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug")

    monkeypatch.setattr("forgeops.cli.doctor_cmd.run_doctor", boom)
    monkeypatch.setenv("FORGEOPS_DEBUG", "0")
    exit_code = main(["doctor", "--repo", str(git_repo)])
    assert exit_code == exit_codes.INTERNAL_ERROR


def test_known_repo_not_found_error_keeps_its_own_exit_code(tmp_path, capsys):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    exit_code = main(["status", "--repo", str(outside)])
    assert exit_code == exit_codes.REPO_NOT_FOUND


def test_known_invalid_config_error_keeps_its_own_exit_code(git_repo):
    (git_repo / "pyproject.toml").write_text("not [ valid toml", encoding="utf-8")
    exit_code = main(["doctor", "--repo", str(git_repo)])
    assert exit_code == exit_codes.INVALID_CONFIG


def test_missing_repo_context_skips_log_write_gracefully(tmp_path, monkeypatch, capsys):
    """An internal error with no discoverable repository root at all must
    not itself crash while trying to write a diagnostic log."""
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug")

    monkeypatch.setattr("forgeops.cli.doctor_cmd.run_doctor", boom)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    exit_code = main(["doctor", "--repo", str(outside)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "diagnostic log not written" in captured.err
