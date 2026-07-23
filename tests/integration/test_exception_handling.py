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


# --- Phase 2C: forgeops test --full and forgeops release-check share the
# same top-level boundary, exercised here explicitly per command. ---

def test_full_test_unexpected_exception_returns_internal_error(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in full test")

    monkeypatch.setattr("forgeops.cli.test_cmd.run_full_test", boom)
    exit_code = main(["test", "--full", "--repo", str(git_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in full test" in captured.err


def test_full_test_unexpected_exception_json_mode(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in full test")

    monkeypatch.setattr("forgeops.cli.test_cmd.run_full_test", boom)
    exit_code = main(["test", "--full", "--repo", str(git_repo), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_full_test_debug_flag_lets_exception_propagate(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in full test")

    monkeypatch.setattr("forgeops.cli.test_cmd.run_full_test", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in full test"):
        main(["--debug", "test", "--full", "--repo", str(git_repo)])


def test_release_check_unexpected_exception_returns_internal_error(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in release-check")

    monkeypatch.setattr("forgeops.cli.release_check_cmd.run_release_check", boom)
    exit_code = main(["release-check", "--repo", str(git_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in release-check" in captured.err


def test_release_check_unexpected_exception_json_mode(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in release-check")

    monkeypatch.setattr("forgeops.cli.release_check_cmd.run_release_check", boom)
    exit_code = main(["release-check", "--repo", str(git_repo), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_release_check_diagnostic_log_redacted(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("leaked postgres://user:hunter2@host/db in the error")  # forgeops:allow-secret

    monkeypatch.setattr("forgeops.cli.release_check_cmd.run_release_check", boom)
    main(["release-check", "--repo", str(git_repo)])
    log_files = list((git_repo / "logs" / "release-check").glob("*/internal_error.log"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert "hunter2" not in content
    assert "[REDACTED_DB_URL]" in content


def test_release_check_debug_flag_lets_exception_propagate(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in release-check")

    monkeypatch.setattr("forgeops.cli.release_check_cmd.run_release_check", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in release-check"):
        main(["--debug", "release-check", "--repo", str(git_repo)])


def test_checkpoint_unexpected_exception_returns_internal_error(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in checkpoint")

    monkeypatch.setattr("forgeops.cli.checkpoint_cmd.run_checkpoint", boom)
    exit_code = main(["checkpoint", "--repo", str(git_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in checkpoint" in captured.err
    # A crash before the write step must never leave a partial state file.
    assert not (git_repo / ".agent" / "CURRENT_STATE.json").exists()


def test_checkpoint_unexpected_exception_json_mode(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in checkpoint")

    monkeypatch.setattr("forgeops.cli.checkpoint_cmd.run_checkpoint", boom)
    exit_code = main(["checkpoint", "--repo", str(git_repo), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_checkpoint_debug_flag_lets_exception_propagate(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in checkpoint")

    monkeypatch.setattr("forgeops.cli.checkpoint_cmd.run_checkpoint", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in checkpoint"):
        main(["--debug", "checkpoint", "--repo", str(git_repo)])


def test_handoff_unexpected_exception_returns_internal_error(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in handoff")

    monkeypatch.setattr("forgeops.cli.handoff_cmd.run_handoff", boom)
    exit_code = main(["handoff", "--repo", str(git_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in handoff" in captured.err
    assert not (git_repo / ".agent" / "HANDOFF.md").exists()


def test_handoff_unexpected_exception_json_mode(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in handoff")

    monkeypatch.setattr("forgeops.cli.handoff_cmd.run_handoff", boom)
    exit_code = main(["handoff", "--repo", str(git_repo), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_handoff_debug_flag_lets_exception_propagate(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in handoff")

    monkeypatch.setattr("forgeops.cli.handoff_cmd.run_handoff", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in handoff"):
        main(["--debug", "handoff", "--repo", str(git_repo)])


def test_checkpoint_dry_run_flag_reaches_run_checkpoint(git_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_checkpoint(repo_arg, dry_run=False):
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.checkpoint import run_checkpoint as real
        return real(repo_arg, write_log=False, dry_run=dry_run)

    monkeypatch.setattr("forgeops.cli.checkpoint_cmd.run_checkpoint", fake_run_checkpoint)
    main(["checkpoint", "--repo", str(git_repo), "--dry-run"])
    assert captured_kwargs["dry_run"] is True


def test_handoff_dry_run_flag_reaches_run_handoff(git_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_handoff(repo_arg, dry_run=False):
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.handoff import run_handoff as real
        return real(repo_arg, write_log=False, dry_run=dry_run)

    monkeypatch.setattr("forgeops.cli.handoff_cmd.run_handoff", fake_run_handoff)
    main(["handoff", "--repo", str(git_repo), "--dry-run"])
    assert captured_kwargs["dry_run"] is True


def test_process_list_unexpected_exception_returns_internal_error(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in process-list")

    monkeypatch.setattr("forgeops.cli.process_list_cmd.run_process_list", boom)
    exit_code = main(["process-list", "--repo", str(git_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in process-list" in captured.err


def test_process_list_unexpected_exception_json_mode(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in process-list")

    monkeypatch.setattr("forgeops.cli.process_list_cmd.run_process_list", boom)
    exit_code = main(["process-list", "--repo", str(git_repo), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_process_list_debug_flag_lets_exception_propagate(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in process-list")

    monkeypatch.setattr("forgeops.cli.process_list_cmd.run_process_list", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in process-list"):
        main(["--debug", "process-list", "--repo", str(git_repo)])


def test_cleanup_unexpected_exception_returns_internal_error(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in cleanup")

    monkeypatch.setattr("forgeops.cli.cleanup_cmd.run_cleanup", boom)
    exit_code = main(["cleanup", "--repo", str(git_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in cleanup" in captured.err
    # A crash must never leave a partial/corrupted registry behind.
    assert not (git_repo / ".agent" / "runtime" / "PROCESS_REGISTRY.json").exists()


def test_cleanup_unexpected_exception_json_mode(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in cleanup")

    monkeypatch.setattr("forgeops.cli.cleanup_cmd.run_cleanup", boom)
    exit_code = main(["cleanup", "--repo", str(git_repo), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_cleanup_debug_flag_lets_exception_propagate(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in cleanup")

    monkeypatch.setattr("forgeops.cli.cleanup_cmd.run_cleanup", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in cleanup"):
        main(["--debug", "cleanup", "--repo", str(git_repo)])


def test_cleanup_execute_flag_reaches_run_cleanup(git_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_cleanup(repo_arg, execute=False):
        captured_kwargs["execute"] = execute
        from forgeops.cli.cleanup import run_cleanup as real
        return real(repo_arg, write_log=False, execute=execute)

    monkeypatch.setattr("forgeops.cli.cleanup_cmd.run_cleanup", fake_run_cleanup)
    main(["cleanup", "--repo", str(git_repo), "--execute"])
    assert captured_kwargs["execute"] is True


def test_cleanup_dry_run_flag_overrides_execute_for_safety(git_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_cleanup(repo_arg, execute=False):
        captured_kwargs["execute"] = execute
        from forgeops.cli.cleanup import run_cleanup as real
        return real(repo_arg, write_log=False, execute=execute)

    monkeypatch.setattr("forgeops.cli.cleanup_cmd.run_cleanup", fake_run_cleanup)
    main(["cleanup", "--repo", str(git_repo), "--execute", "--dry-run"])
    assert captured_kwargs["execute"] is False


def test_cleanup_default_is_dry_run(git_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_cleanup(repo_arg, execute=False):
        captured_kwargs["execute"] = execute
        from forgeops.cli.cleanup import run_cleanup as real
        return real(repo_arg, write_log=False, execute=execute)

    monkeypatch.setattr("forgeops.cli.cleanup_cmd.run_cleanup", fake_run_cleanup)
    main(["cleanup", "--repo", str(git_repo)])
    assert captured_kwargs["execute"] is False


def test_resume_context_unexpected_exception_returns_internal_error(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in resume-context")

    monkeypatch.setattr("forgeops.cli.resume_context_cmd.run_resume_context", boom)
    exit_code = main(["resume-context", "--repo", str(git_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in resume-context" in captured.err


def test_resume_context_unexpected_exception_json_mode(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in resume-context")

    monkeypatch.setattr("forgeops.cli.resume_context_cmd.run_resume_context", boom)
    exit_code = main(["resume-context", "--repo", str(git_repo), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_resume_context_debug_flag_lets_exception_propagate(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in resume-context")

    monkeypatch.setattr("forgeops.cli.resume_context_cmd.run_resume_context", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in resume-context"):
        main(["--debug", "resume-context", "--repo", str(git_repo)])


def test_test_command_requires_a_mode_flag(git_repo, capsys):
    exit_code = main(["test", "--repo", str(git_repo)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "--targeted or --full" in captured.err


def test_test_command_rejects_both_mode_flags_at_once(git_repo):
    with pytest.raises(SystemExit):
        main(["test", "--targeted", "--full", "--repo", str(git_repo)])
