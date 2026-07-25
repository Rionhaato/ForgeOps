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


def test_init_unexpected_exception_returns_internal_error(tmp_path, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in init")

    monkeypatch.setattr("forgeops.cli.init_cmd.run_init", boom)
    exit_code = main(["init", str(tmp_path)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in init" in captured.err
    # A crash before any write must never leave a partial governance structure.
    assert not (tmp_path / ".agent").exists()
    assert not (tmp_path / "CLAUDE.md").exists()


def test_init_unexpected_exception_json_mode(tmp_path, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in init")

    monkeypatch.setattr("forgeops.cli.init_cmd.run_init", boom)
    exit_code = main(["init", str(tmp_path), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_init_debug_flag_lets_exception_propagate(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in init")

    monkeypatch.setattr("forgeops.cli.init_cmd.run_init", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in init"):
        main(["--debug", "init", str(tmp_path)])


def test_init_dry_run_flag_reaches_run_init(tmp_path, monkeypatch):
    captured_kwargs = {}

    def fake_run_init(path_arg, dry_run=False):
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.init import run_init as real
        return real(path_arg, write_log=False, dry_run=dry_run)

    monkeypatch.setattr("forgeops.cli.init_cmd.run_init", fake_run_init)
    main(["init", str(tmp_path), "--dry-run"])
    assert captured_kwargs["dry_run"] is True


def test_init_omitted_path_argument_is_passed_as_none(tmp_path, monkeypatch):
    captured_kwargs = {}

    def fake_run_init(path_arg, dry_run=False):
        captured_kwargs["path_arg"] = path_arg
        from forgeops.cli.init import run_init as real
        return real(path_arg, cwd=tmp_path, write_log=False, dry_run=dry_run)

    monkeypatch.setattr("forgeops.cli.init_cmd.run_init", fake_run_init)
    monkeypatch.chdir(tmp_path)
    main(["init"])
    assert captured_kwargs["path_arg"] is None


def test_worktree_list_unexpected_exception_returns_internal_error(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in worktree list")

    monkeypatch.setattr("forgeops.cli.worktree_cmd.run_worktree_list", boom)
    exit_code = main(["worktree", "list", "--repo", str(git_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in worktree list" in captured.err


def test_worktree_list_unexpected_exception_json_mode(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in worktree list")

    monkeypatch.setattr("forgeops.cli.worktree_cmd.run_worktree_list", boom)
    exit_code = main(["worktree", "list", "--repo", str(git_repo), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_worktree_list_debug_flag_lets_exception_propagate(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in worktree list")

    monkeypatch.setattr("forgeops.cli.worktree_cmd.run_worktree_list", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in worktree list"):
        main(["--debug", "worktree", "list", "--repo", str(git_repo)])


def test_worktree_create_unexpected_exception_returns_internal_error(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in worktree create")

    monkeypatch.setattr("forgeops.cli.worktree_cmd.run_worktree_create", boom)
    exit_code = main(["worktree", "create", "demo", "--repo", str(git_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in worktree create" in captured.err
    # A crash before any git mutation must never leave a partial worktree behind.
    assert not (git_repo.parent / ".forgeops-worktrees").exists()


def test_worktree_create_unexpected_exception_json_mode(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in worktree create")

    monkeypatch.setattr("forgeops.cli.worktree_cmd.run_worktree_create", boom)
    exit_code = main(["worktree", "create", "demo", "--repo", str(git_repo), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_worktree_create_debug_flag_lets_exception_propagate(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in worktree create")

    monkeypatch.setattr("forgeops.cli.worktree_cmd.run_worktree_create", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in worktree create"):
        main(["--debug", "worktree", "create", "demo", "--repo", str(git_repo)])


def test_worktree_create_dry_run_flag_reaches_run_worktree_create(git_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_worktree_create(name_arg, repo_arg, dry_run=False, branch=None, base=None):
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.worktree import run_worktree_create as real
        return real(name_arg, repo_arg, write_log=False, dry_run=dry_run, branch=branch, base=base)

    monkeypatch.setattr("forgeops.cli.worktree_cmd.run_worktree_create", fake_run_worktree_create)
    main(["worktree", "create", "demo", "--repo", str(git_repo), "--dry-run"])
    assert captured_kwargs["dry_run"] is True


def test_worktree_create_branch_and_base_flags_reach_run_worktree_create(git_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_worktree_create(name_arg, repo_arg, dry_run=False, branch=None, base=None):
        captured_kwargs["branch"] = branch
        captured_kwargs["base"] = base
        from forgeops.cli.worktree import run_worktree_create as real
        return real(name_arg, repo_arg, write_log=False, dry_run=True, branch=branch, base=base)

    monkeypatch.setattr("forgeops.cli.worktree_cmd.run_worktree_create", fake_run_worktree_create)
    main(["worktree", "create", "demo", "--repo", str(git_repo), "--branch", "my-branch", "--base", "HEAD", "--dry-run"])
    assert captured_kwargs["branch"] == "my-branch"
    assert captured_kwargs["base"] == "HEAD"


def test_worktree_remove_unexpected_exception_returns_internal_error(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in worktree remove")

    monkeypatch.setattr("forgeops.cli.worktree_cmd.run_worktree_remove", boom)
    exit_code = main(["worktree", "remove", "demo", "--repo", str(git_repo), "--confirm"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in worktree remove" in captured.err


def test_worktree_remove_unexpected_exception_json_mode(git_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in worktree remove")

    monkeypatch.setattr("forgeops.cli.worktree_cmd.run_worktree_remove", boom)
    exit_code = main(["worktree", "remove", "demo", "--repo", str(git_repo), "--confirm", "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_worktree_remove_debug_flag_lets_exception_propagate(git_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in worktree remove")

    monkeypatch.setattr("forgeops.cli.worktree_cmd.run_worktree_remove", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in worktree remove"):
        main(["--debug", "worktree", "remove", "demo", "--repo", str(git_repo), "--confirm"])


def test_worktree_remove_dry_run_flag_reaches_run_worktree_remove(git_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_worktree_remove(name_arg, repo_arg, dry_run=False, confirm=False, delete_branch=False):
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.worktree import run_worktree_remove as real
        return real(name_arg, repo_arg, write_log=False, dry_run=dry_run, confirm=confirm, delete_branch=delete_branch)

    monkeypatch.setattr("forgeops.cli.worktree_cmd.run_worktree_remove", fake_run_worktree_remove)
    main(["worktree", "remove", "demo", "--repo", str(git_repo), "--dry-run"])
    assert captured_kwargs["dry_run"] is True


def test_worktree_remove_confirm_and_delete_branch_flags_reach_run_worktree_remove(git_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_worktree_remove(name_arg, repo_arg, dry_run=False, confirm=False, delete_branch=False):
        captured_kwargs["confirm"] = confirm
        captured_kwargs["delete_branch"] = delete_branch
        from forgeops.cli.worktree import run_worktree_remove as real
        return real(name_arg, repo_arg, write_log=False, dry_run=dry_run, confirm=confirm, delete_branch=delete_branch)

    monkeypatch.setattr("forgeops.cli.worktree_cmd.run_worktree_remove", fake_run_worktree_remove)
    main(["worktree", "remove", "demo", "--repo", str(git_repo), "--confirm", "--delete-branch"])
    assert captured_kwargs["confirm"] is True
    assert captured_kwargs["delete_branch"] is True


def test_test_command_requires_a_mode_flag(git_repo, capsys):
    exit_code = main(["test", "--repo", str(git_repo)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "--targeted or --full" in captured.err


def test_test_command_rejects_both_mode_flags_at_once(git_repo):
    with pytest.raises(SystemExit):
        main(["test", "--targeted", "--full", "--repo", str(git_repo)])


def test_task_create_unexpected_exception_returns_internal_error(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task create")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_create", boom)
    exit_code = main(["task", "create", "My Task", "--repo", str(initialized_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in task create" in captured.err
    # A crash before any write must never leave a partial task directory behind.
    assert not (initialized_repo / ".agent" / "tasks" / "task-0001").exists()


def test_task_create_unexpected_exception_json_mode(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task create")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_create", boom)
    exit_code = main(["task", "create", "My Task", "--repo", str(initialized_repo), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_task_create_debug_flag_lets_exception_propagate(initialized_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task create")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_create", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in task create"):
        main(["--debug", "task", "create", "My Task", "--repo", str(initialized_repo)])


def test_task_create_dry_run_flag_reaches_run_task_create(initialized_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_task_create(title, repo_arg, dry_run=False, spec_file=None, acceptance_file=None):
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.task import run_task_create as real
        return real(title, repo_arg, write_log=False, dry_run=dry_run, spec_file=spec_file, acceptance_file=acceptance_file)

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_create", fake_run_task_create)
    main(["task", "create", "My Task", "--repo", str(initialized_repo), "--dry-run"])
    assert captured_kwargs["dry_run"] is True


def test_task_close_unexpected_exception_returns_internal_error(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task close")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_close", boom)
    exit_code = main(["task", "close", "task-0001", "--repo", str(initialized_repo), "--confirm"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in task close" in captured.err


def test_task_close_unexpected_exception_json_mode(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task close")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_close", boom)
    exit_code = main(["task", "close", "task-0001", "--repo", str(initialized_repo), "--confirm", "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR


def test_task_close_debug_flag_lets_exception_propagate(initialized_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task close")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_close", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in task close"):
        main(["--debug", "task", "close", "task-0001", "--repo", str(initialized_repo), "--confirm"])


def test_task_close_confirm_and_dry_run_flags_reach_run_task_close(initialized_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_task_close(task_id, repo_arg, dry_run=False, confirm=False, result_file=None):
        captured_kwargs["confirm"] = confirm
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.task import run_task_close as real
        return real(task_id, repo_arg, write_log=False, dry_run=dry_run, confirm=confirm, result_file=result_file)

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_close", fake_run_task_close)
    main(["task", "close", "task-0001", "--repo", str(initialized_repo), "--confirm"])
    assert captured_kwargs["confirm"] is True
    assert captured_kwargs["dry_run"] is False


def test_task_show_unexpected_exception_returns_internal_error(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task show")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_show", boom)
    exit_code = main(["task", "show", "task-0001", "--repo", str(initialized_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "simulated internal bug in task show" in captured.err


def test_task_command_requires_a_subcommand(initialized_repo):
    with pytest.raises(SystemExit):
        main(["task", "--repo", str(initialized_repo)])


def test_task_assign_unexpected_exception_returns_internal_error(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task assign")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_assign", boom)
    exit_code = main(["task", "assign", "task-0001", "demo", "--repo", str(initialized_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in task assign" in captured.err


def test_task_assign_unexpected_exception_json_mode(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task assign")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_assign", boom)
    exit_code = main(["task", "assign", "task-0001", "demo", "--repo", str(initialized_repo), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_task_assign_debug_flag_lets_exception_propagate(initialized_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task assign")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_assign", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in task assign"):
        main(["--debug", "task", "assign", "task-0001", "demo", "--repo", str(initialized_repo)])


def test_task_assign_dry_run_flag_reaches_run_task_assign(initialized_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_task_assign(task_id, worktree_name, repo_arg, dry_run=False):
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.task import run_task_assign as real
        return real(task_id, worktree_name, repo_arg, write_log=False, dry_run=dry_run)

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_assign", fake_run_task_assign)
    main(["task", "assign", "task-0001", "demo", "--repo", str(initialized_repo), "--dry-run"])
    assert captured_kwargs["dry_run"] is True


def test_task_unassign_unexpected_exception_returns_internal_error(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task unassign")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_unassign", boom)
    exit_code = main(["task", "unassign", "task-0001", "--repo", str(initialized_repo), "--confirm"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "simulated internal bug in task unassign" in captured.err


def test_task_unassign_unexpected_exception_json_mode(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task unassign")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_unassign", boom)
    exit_code = main(["task", "unassign", "task-0001", "--repo", str(initialized_repo), "--confirm", "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR


def test_task_unassign_debug_flag_lets_exception_propagate(initialized_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task unassign")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_unassign", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in task unassign"):
        main(["--debug", "task", "unassign", "task-0001", "--repo", str(initialized_repo), "--confirm"])


def test_task_unassign_confirm_and_dry_run_flags_reach_run_task_unassign(initialized_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_task_unassign(task_id, repo_arg, dry_run=False, confirm=False):
        captured_kwargs["confirm"] = confirm
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.task import run_task_unassign as real
        return real(task_id, repo_arg, write_log=False, dry_run=dry_run, confirm=confirm)

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_unassign", fake_run_task_unassign)
    main(["task", "unassign", "task-0001", "--repo", str(initialized_repo), "--confirm"])
    assert captured_kwargs["confirm"] is True
    assert captured_kwargs["dry_run"] is False


def test_task_assign_agent_unexpected_exception_returns_internal_error(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task assign-agent")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_assign_agent", boom)
    exit_code = main(["task", "assign-agent", "task-0001", "claude-primary", "--repo", str(initialized_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in task assign-agent" in captured.err


def test_task_assign_agent_unexpected_exception_json_mode(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task assign-agent")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_assign_agent", boom)
    exit_code = main(["task", "assign-agent", "task-0001", "claude-primary", "--repo", str(initialized_repo), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_task_assign_agent_debug_flag_lets_exception_propagate(initialized_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task assign-agent")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_assign_agent", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in task assign-agent"):
        main(["--debug", "task", "assign-agent", "task-0001", "claude-primary", "--repo", str(initialized_repo)])


def test_task_assign_agent_dry_run_flag_reaches_run_task_assign_agent(initialized_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_task_assign_agent(task_id, agent_id, repo_arg, dry_run=False):
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.task import run_task_assign_agent as real
        return real(task_id, agent_id, repo_arg, write_log=False, dry_run=dry_run)

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_assign_agent", fake_run_task_assign_agent)
    main(["task", "assign-agent", "task-0001", "claude-primary", "--repo", str(initialized_repo), "--dry-run"])
    assert captured_kwargs["dry_run"] is True


def test_task_unassign_agent_unexpected_exception_returns_internal_error(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task unassign-agent")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_unassign_agent", boom)
    exit_code = main(["task", "unassign-agent", "task-0001", "--repo", str(initialized_repo), "--confirm"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "simulated internal bug in task unassign-agent" in captured.err


def test_task_unassign_agent_unexpected_exception_json_mode(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task unassign-agent")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_unassign_agent", boom)
    exit_code = main(["task", "unassign-agent", "task-0001", "--repo", str(initialized_repo), "--confirm", "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR


def test_task_unassign_agent_debug_flag_lets_exception_propagate(initialized_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task unassign-agent")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_unassign_agent", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in task unassign-agent"):
        main(["--debug", "task", "unassign-agent", "task-0001", "--repo", str(initialized_repo), "--confirm"])


def test_task_unassign_agent_confirm_and_dry_run_flags_reach_run_task_unassign_agent(initialized_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_task_unassign_agent(task_id, repo_arg, dry_run=False, confirm=False):
        captured_kwargs["confirm"] = confirm
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.task import run_task_unassign_agent as real
        return real(task_id, repo_arg, write_log=False, dry_run=dry_run, confirm=confirm)

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_unassign_agent", fake_run_task_unassign_agent)
    main(["task", "unassign-agent", "task-0001", "--repo", str(initialized_repo), "--confirm"])
    assert captured_kwargs["confirm"] is True
    assert captured_kwargs["dry_run"] is False


def test_task_request_approval_unexpected_exception_returns_internal_error(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task request-approval")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_request_approval", boom)
    exit_code = main(["task", "request-approval", "task-0001", "--actor", "joshua", "--repo", str(initialized_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in task request-approval" in captured.err


def test_task_request_approval_unexpected_exception_json_mode(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task request-approval")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_request_approval", boom)
    exit_code = main(["task", "request-approval", "task-0001", "--actor", "joshua", "--repo", str(initialized_repo), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_task_request_approval_debug_flag_lets_exception_propagate(initialized_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task request-approval")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_request_approval", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in task request-approval"):
        main(["--debug", "task", "request-approval", "task-0001", "--actor", "joshua", "--repo", str(initialized_repo)])


def test_task_request_approval_flags_reach_run_task_request_approval(initialized_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run(task_id, repo_arg, actor="", reason=None, dry_run=False):
        captured_kwargs["actor"] = actor
        captured_kwargs["reason"] = reason
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.task import run_task_request_approval as real
        return real(task_id, repo_arg, write_log=False, actor=actor, reason=reason, dry_run=dry_run)

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_request_approval", fake_run)
    main(["task", "request-approval", "task-0001", "--actor", "joshua", "--reason", "please review", "--repo", str(initialized_repo), "--dry-run"])
    assert captured_kwargs["actor"] == "joshua"
    assert captured_kwargs["reason"] == "please review"
    assert captured_kwargs["dry_run"] is True


def test_task_approve_unexpected_exception_returns_internal_error(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task approve")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_approve", boom)
    exit_code = main(["task", "approve", "task-0001", "--actor", "joshua", "--repo", str(initialized_repo), "--confirm"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "simulated internal bug in task approve" in captured.err


def test_task_approve_unexpected_exception_json_mode(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task approve")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_approve", boom)
    exit_code = main(["task", "approve", "task-0001", "--actor", "joshua", "--repo", str(initialized_repo), "--confirm", "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR


def test_task_approve_debug_flag_lets_exception_propagate(initialized_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task approve")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_approve", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in task approve"):
        main(["--debug", "task", "approve", "task-0001", "--actor", "joshua", "--repo", str(initialized_repo), "--confirm"])


def test_task_approve_confirm_and_dry_run_flags_reach_run_task_approve(initialized_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run(task_id, repo_arg, actor="", reason=None, dry_run=False, confirm=False):
        captured_kwargs["confirm"] = confirm
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.task import run_task_approve as real
        return real(task_id, repo_arg, write_log=False, actor=actor, reason=reason, dry_run=dry_run, confirm=confirm)

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_approve", fake_run)
    main(["task", "approve", "task-0001", "--actor", "joshua", "--repo", str(initialized_repo), "--confirm"])
    assert captured_kwargs["confirm"] is True
    assert captured_kwargs["dry_run"] is False


def test_task_reject_unexpected_exception_returns_internal_error(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task reject")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_reject", boom)
    exit_code = main(["task", "reject", "task-0001", "--actor", "joshua", "--reason", "no", "--repo", str(initialized_repo), "--confirm"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "simulated internal bug in task reject" in captured.err


def test_task_reject_unexpected_exception_json_mode(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task reject")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_reject", boom)
    exit_code = main(["task", "reject", "task-0001", "--actor", "joshua", "--reason", "no", "--repo", str(initialized_repo), "--confirm", "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR


def test_task_reject_debug_flag_lets_exception_propagate(initialized_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task reject")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_reject", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in task reject"):
        main(["--debug", "task", "reject", "task-0001", "--actor", "joshua", "--reason", "no", "--repo", str(initialized_repo), "--confirm"])


def test_task_reject_confirm_and_dry_run_flags_reach_run_task_reject(initialized_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run(task_id, repo_arg, actor="", reason=None, dry_run=False, confirm=False):
        captured_kwargs["confirm"] = confirm
        captured_kwargs["dry_run"] = dry_run
        captured_kwargs["reason"] = reason
        from forgeops.cli.task import run_task_reject as real
        return real(task_id, repo_arg, write_log=False, actor=actor, reason=reason, dry_run=dry_run, confirm=confirm)

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_reject", fake_run)
    main(["task", "reject", "task-0001", "--actor", "joshua", "--reason", "no", "--repo", str(initialized_repo), "--confirm"])
    assert captured_kwargs["confirm"] is True
    assert captured_kwargs["dry_run"] is False
    assert captured_kwargs["reason"] == "no"


def test_task_cancel_approval_unexpected_exception_returns_internal_error(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task cancel-approval")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_cancel_approval", boom)
    exit_code = main(["task", "cancel-approval", "task-0001", "--actor", "joshua", "--repo", str(initialized_repo), "--confirm"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "simulated internal bug in task cancel-approval" in captured.err


def test_task_cancel_approval_unexpected_exception_json_mode(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task cancel-approval")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_cancel_approval", boom)
    exit_code = main(["task", "cancel-approval", "task-0001", "--actor", "joshua", "--repo", str(initialized_repo), "--confirm", "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR


def test_task_cancel_approval_debug_flag_lets_exception_propagate(initialized_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in task cancel-approval")

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_cancel_approval", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in task cancel-approval"):
        main(["--debug", "task", "cancel-approval", "task-0001", "--actor", "joshua", "--repo", str(initialized_repo), "--confirm"])


def test_task_cancel_approval_confirm_and_dry_run_flags_reach_run_task_cancel_approval(initialized_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run(task_id, repo_arg, actor="", reason=None, dry_run=False, confirm=False):
        captured_kwargs["confirm"] = confirm
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.task import run_task_cancel_approval as real
        return real(task_id, repo_arg, write_log=False, actor=actor, reason=reason, dry_run=dry_run, confirm=confirm)

    monkeypatch.setattr("forgeops.cli.task_cmd.run_task_cancel_approval", fake_run)
    main(["task", "cancel-approval", "task-0001", "--actor", "joshua", "--repo", str(initialized_repo), "--confirm"])
    assert captured_kwargs["confirm"] is True
    assert captured_kwargs["dry_run"] is False


def test_agent_register_unexpected_exception_returns_internal_error(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in agent register")

    monkeypatch.setattr("forgeops.cli.agent_cmd.run_agent_register", boom)
    exit_code = main(["agent", "register", "claude-primary", "--kind", "claude", "--repo", str(initialized_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "Traceback (most recent call last)" not in captured.err
    assert "simulated internal bug in agent register" in captured.err
    # A crash before any write must never leave a partial registry behind.
    assert not (initialized_repo / ".agent" / "agents" / "AGENT_REGISTRY.json").exists()


def test_agent_register_unexpected_exception_json_mode(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in agent register")

    monkeypatch.setattr("forgeops.cli.agent_cmd.run_agent_register", boom)
    exit_code = main(["agent", "register", "claude-primary", "--kind", "claude", "--repo", str(initialized_repo), "--json"])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    payload = json.loads(captured.out)
    assert payload["exit_code"] == exit_codes.INTERNAL_ERROR
    assert payload["error"] == "RuntimeError"


def test_agent_register_debug_flag_lets_exception_propagate(initialized_repo, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in agent register")

    monkeypatch.setattr("forgeops.cli.agent_cmd.run_agent_register", boom)
    with pytest.raises(RuntimeError, match="simulated internal bug in agent register"):
        main(["--debug", "agent", "register", "claude-primary", "--kind", "claude", "--repo", str(initialized_repo)])


def test_agent_register_dry_run_flag_reaches_run_agent_register(initialized_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_agent_register(agent_id, kind, repo_arg, dry_run=False, display_name=None):
        captured_kwargs["dry_run"] = dry_run
        from forgeops.cli.agent import run_agent_register as real
        return real(agent_id, kind, repo_arg, write_log=False, dry_run=dry_run, display_name=display_name)

    monkeypatch.setattr("forgeops.cli.agent_cmd.run_agent_register", fake_run_agent_register)
    main(["agent", "register", "claude-primary", "--kind", "claude", "--repo", str(initialized_repo), "--dry-run"])
    assert captured_kwargs["dry_run"] is True


def test_agent_register_display_name_flag_reaches_run_agent_register(initialized_repo, monkeypatch):
    captured_kwargs = {}

    def fake_run_agent_register(agent_id, kind, repo_arg, dry_run=False, display_name=None):
        captured_kwargs["display_name"] = display_name
        from forgeops.cli.agent import run_agent_register as real
        return real(agent_id, kind, repo_arg, write_log=False, dry_run=True, display_name=display_name)

    monkeypatch.setattr("forgeops.cli.agent_cmd.run_agent_register", fake_run_agent_register)
    main(["agent", "register", "claude-primary", "--kind", "claude", "--repo", str(initialized_repo), "--display-name", "Primary Claude", "--dry-run"])
    assert captured_kwargs["display_name"] == "Primary Claude"


def test_agent_show_unexpected_exception_returns_internal_error(initialized_repo, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated internal bug in agent show")

    monkeypatch.setattr("forgeops.cli.agent_cmd.run_agent_show", boom)
    exit_code = main(["agent", "show", "claude-primary", "--repo", str(initialized_repo)])
    captured = capsys.readouterr()
    assert exit_code == exit_codes.INTERNAL_ERROR
    assert "simulated internal bug in agent show" in captured.err


def test_agent_command_requires_a_subcommand(initialized_repo):
    with pytest.raises(SystemExit):
        main(["agent", "--repo", str(initialized_repo)])
