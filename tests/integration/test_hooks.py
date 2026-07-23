"""Integration tests for the project-local Claude Code hooks in
`.claude/hooks/`. Each hook is invoked exactly as Claude Code would invoke
it (a subprocess fed JSON on stdin), with harmless simulated tool-call
payloads - no real destructive command is ever executed; only the hook's
own decision output is checked."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "hooks"
PRETOOLUSE_HOOK = HOOKS_DIR / "pretooluse_safety.py"
SESSIONEND_HOOK = HOOKS_DIR / "sessionend_handoff.py"
HOOK_TIMEOUT_SECONDS = 15


def _run_hook(script: Path, payload: dict | None, raw_stdin: str | None = None) -> subprocess.CompletedProcess:
    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(payload or {})
    return subprocess.run(
        [sys.executable, str(script)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=HOOK_TIMEOUT_SECONDS,
    )


def _decision(proc: subprocess.CompletedProcess) -> dict | None:
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)


# --- PreToolUse: TrendForge protection ---------------------------------

def test_trendforge_write_via_bash_is_denied():
    proc = _run_hook(PRETOOLUSE_HOOK, {
        "tool_name": "Bash",
        "tool_input": {"command": r"echo hack > C:\Users\joshd\TrendForge\.env"},
    })
    decision = _decision(proc)
    assert decision is not None
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "TrendForge" in decision["hookSpecificOutput"]["permissionDecisionReason"]


def test_trendforge_write_via_edit_tool_is_denied():
    proc = _run_hook(PRETOOLUSE_HOOK, {
        "tool_name": "Edit",
        "tool_input": {"file_path": r"C:\Users\joshd\TrendForge\backend\main.py"},
    })
    decision = _decision(proc)
    assert decision is not None
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_trendforge_read_only_command_is_allowed():
    proc = _run_hook(PRETOOLUSE_HOOK, {
        "tool_name": "Bash",
        "tool_input": {"command": r"git -C C:\Users\joshd\TrendForge status"},
    })
    assert _decision(proc) is None
    assert proc.returncode == 0


# --- PreToolUse: destructive git operations -----------------------------

def test_git_reset_hard_is_denied():
    proc = _run_hook(PRETOOLUSE_HOOK, {"tool_name": "Bash", "tool_input": {"command": "git reset --hard origin/main"}})
    decision = _decision(proc)
    assert decision is not None
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_git_clean_fdx_is_denied():
    proc = _run_hook(PRETOOLUSE_HOOK, {"tool_name": "Bash", "tool_input": {"command": "git clean -fdx"}})
    assert _decision(proc)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_git_force_push_is_denied():
    proc = _run_hook(PRETOOLUSE_HOOK, {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}})
    assert _decision(proc)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_git_checkout_force_restore_is_denied():
    proc = _run_hook(PRETOOLUSE_HOOK, {"tool_name": "Bash", "tool_input": {"command": "git checkout -- ."}})
    assert _decision(proc)["hookSpecificOutput"]["permissionDecision"] == "deny"


# --- PreToolUse: safe commands pass through ------------------------------

def test_safe_read_only_command_is_allowed():
    proc = _run_hook(PRETOOLUSE_HOOK, {"tool_name": "Bash", "tool_input": {"command": "git status --short"}})
    assert _decision(proc) is None
    assert proc.returncode == 0


def test_ordinary_edit_outside_trendforge_is_allowed():
    proc = _run_hook(PRETOOLUSE_HOOK, {"tool_name": "Edit", "tool_input": {"file_path": r"C:\Users\joshd\ForgeOps\forgeops\cli\doctor.py"}})
    assert _decision(proc) is None


# --- PreToolUse: secrets are never echoed --------------------------------

def test_hook_never_echoes_secret_bearing_command_text():
    secret = "SUPERSECRETVALUE12345"
    proc = _run_hook(PRETOOLUSE_HOOK, {
        "tool_name": "Bash",
        "tool_input": {"command": f"curl -H 'Authorization: Bearer {secret}' C:\\Users\\joshd\\TrendForge\\api"},
    })
    assert secret not in proc.stdout
    assert secret not in proc.stderr


# --- PreToolUse: malformed input fails safely ----------------------------

def test_malformed_json_input_fails_safely():
    proc = _run_hook(PRETOOLUSE_HOOK, None, raw_stdin="{not valid json")
    assert proc.returncode == 0
    assert _decision(proc) is None
    assert "malformed" in proc.stderr.lower()


def test_empty_input_fails_safely():
    proc = _run_hook(PRETOOLUSE_HOOK, None, raw_stdin="")
    assert proc.returncode == 0
    assert _decision(proc) is None


# --- SessionEnd handoff writer -------------------------------------------

def test_sessionend_hook_writes_handoff_and_is_bounded(git_repo):
    proc = _run_hook(SESSIONEND_HOOK, {"cwd": str(git_repo), "hook_event_name": "SessionEnd", "reason": "clear"})
    assert proc.returncode == 0
    assert (git_repo / ".agent" / "HANDOFF.md").is_file()


def test_sessionend_hook_supports_windows_paths_with_spaces(spacey_git_repo):
    proc = _run_hook(SESSIONEND_HOOK, {"cwd": str(spacey_git_repo), "hook_event_name": "SessionEnd", "reason": "clear"})
    assert proc.returncode == 0
    assert (spacey_git_repo / ".agent" / "HANDOFF.md").is_file()


def test_sessionend_hook_never_runs_pytest(git_repo, monkeypatch):
    # Sanity: forgeops handoff's own command list never includes pytest -
    # confirmed structurally rather than by timing, since timing is flaky.
    text = SESSIONEND_HOOK.read_text(encoding="utf-8")
    assert "pytest" not in text
    assert "commit" not in text.lower() or "never commit" in text.lower()


def test_sessionend_hook_malformed_input_fails_safely():
    proc = _run_hook(SESSIONEND_HOOK, None, raw_stdin="{not valid json")
    assert proc.returncode == 0
    assert "malformed" in proc.stderr.lower()


def test_sessionend_hook_missing_cwd_fails_safely():
    proc = _run_hook(SESSIONEND_HOOK, {"hook_event_name": "SessionEnd"})
    assert proc.returncode == 0
    assert "cwd" in proc.stderr.lower()
