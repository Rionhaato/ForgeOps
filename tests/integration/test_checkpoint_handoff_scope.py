"""Regression tests: adding `forgeops checkpoint`/`forgeops handoff` must
not change the behavior of any existing command, and the two new
commands must only ever touch their documented `.agent/` state files
(plus the same `logs/<command>/` side-channel every other command
already uses) - never application source, never anything else in the
working tree."""
from __future__ import annotations

import subprocess
from pathlib import Path

from forgeops.cli.audit import run_audit
from forgeops.cli.changed import run_changed
from forgeops.cli.checkpoint import run_checkpoint
from forgeops.cli.doctor import run_doctor
from forgeops.cli.handoff import run_handoff
from forgeops.cli.release_check import run_release_check
from forgeops.cli.status import run_status
from forgeops.cli.test import run_full_test, run_test_targeted
from forgeops.core import exit_codes


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _snapshot(root: Path) -> dict[str, str]:
    """path (relative, posix) -> content, for every tracked-or-not file
    except logs/ and .git/ (every command is allowed to write logs/)."""
    snapshot = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/") or rel.startswith("logs/"):
            continue
        try:
            snapshot[rel] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            snapshot[rel] = "<unreadable>"
    return snapshot


def test_targeted_test_unchanged_by_this_phase(git_repo):
    result = run_test_targeted(str(git_repo), write_log=False)
    assert result.command == "test"
    assert result.exit_code in (exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT)


def test_full_test_unchanged_by_this_phase(git_repo):
    result = run_full_test(str(git_repo), write_log=False)
    assert result.command == "test"


def test_release_check_unchanged_by_this_phase(git_repo):
    result = run_release_check(str(git_repo), write_log=False)
    assert result.command == "release-check"


def test_inspection_commands_remain_read_only(git_repo):
    before = _snapshot(git_repo)
    run_doctor(str(git_repo), write_log=False)
    run_status(str(git_repo), write_log=False)
    run_audit(str(git_repo), write_log=False)
    run_changed(str(git_repo), write_log=False)
    after = _snapshot(git_repo)
    assert before == after


def test_checkpoint_only_creates_documented_state_path(git_repo):
    before = _snapshot(git_repo)
    run_checkpoint(str(git_repo), write_log=False)
    after = _snapshot(git_repo)

    added_or_changed = {k for k in after if before.get(k) != after.get(k)}
    assert added_or_changed == {".agent/CURRENT_STATE.json"}


def test_handoff_only_creates_documented_state_path(git_repo):
    before = _snapshot(git_repo)
    run_handoff(str(git_repo), write_log=False)
    after = _snapshot(git_repo)

    added_or_changed = {k for k in after if before.get(k) != after.get(k)}
    assert added_or_changed == {".agent/HANDOFF.md"}


def test_checkpoint_then_handoff_only_touch_their_two_documented_files(git_repo):
    before = _snapshot(git_repo)
    run_checkpoint(str(git_repo), write_log=False)
    run_handoff(str(git_repo), write_log=False)
    after = _snapshot(git_repo)

    added_or_changed = {k for k in after if before.get(k) != after.get(k)}
    assert added_or_changed == {".agent/CURRENT_STATE.json", ".agent/HANDOFF.md"}


def test_checkpoint_never_touches_source_files(git_repo):
    (git_repo / "some_source.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], git_repo)
    _git(["commit", "-q", "-m", "add source"], git_repo)
    before_source = (git_repo / "some_source.py").read_text(encoding="utf-8")
    run_checkpoint(str(git_repo), write_log=False)
    run_handoff(str(git_repo), write_log=False)
    after_source = (git_repo / "some_source.py").read_text(encoding="utf-8")
    assert before_source == after_source


def test_checkpoint_and_handoff_never_touch_git_state(git_repo):
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(git_repo), capture_output=True, text=True, check=True
    ).stdout.strip()
    run_checkpoint(str(git_repo), write_log=False)
    run_handoff(str(git_repo), write_log=False)
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(git_repo), capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head_before == head_after
    # Neither command stages anything either.
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(git_repo), capture_output=True, text=True, check=True
    ).stdout
    for line in status.splitlines():
        assert line[0] in (" ", "?")  # nothing in the index column (staged)
