"""Deterministic structural checks for the Context-Efficiency Foundation
checkpoint's skills and subagent: project-local placement, a concise
size ceiling, no duplicated full governance text, and the required
stop/approval/read-only language. These are plain file-content checks -
no forgeops import needed - so they stay fast and independent of the
package under test."""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _normalize(text: str) -> str:
    """Collapse whitespace/newlines so a phrase hand-wrapped across
    markdown lines still matches as a contiguous substring."""
    return re.sub(r"\s+", " ", text)
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

SKILL_SIZE_CEILING_BYTES = 2500
AGENT_SIZE_CEILING_BYTES = 4000

SKILLS = {
    "forgeops-resume": REPO_ROOT / ".claude" / "skills" / "forgeops-resume" / "SKILL.md",
    "forgeops-validate": REPO_ROOT / ".claude" / "skills" / "forgeops-validate" / "SKILL.md",
    "forgeops-completion-report": REPO_ROOT / ".claude" / "skills" / "forgeops-completion-report" / "SKILL.md",
}

AGENT_PATH = REPO_ROOT / ".claude" / "agents" / "forgeops-recovery-reviewer.md"


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file not found: {path}"
    return path.read_text(encoding="utf-8")


# --- skills: placement, size, no duplicated governance, stop language ---

def test_all_three_skills_exist_at_expected_project_local_paths():
    for name, path in SKILLS.items():
        assert path.is_file(), f"missing skill file for {name}: {path}"


def test_skill_files_stay_under_the_size_ceiling():
    for name, path in SKILLS.items():
        size = len(_read(path).encode("utf-8"))
        assert size <= SKILL_SIZE_CEILING_BYTES, f"{name} SKILL.md is {size} bytes, over the {SKILL_SIZE_CEILING_BYTES}-byte ceiling"


def test_skill_files_do_not_duplicate_full_claude_md_text():
    claude_md_text = CLAUDE_MD.read_text(encoding="utf-8")
    # A generous, distinctive slice of CLAUDE.md's own prose - if this
    # much of it appears verbatim in a skill file, that skill copied
    # governance text instead of referencing it.
    distinctive_slice = claude_md_text[200:400]
    for name, path in SKILLS.items():
        assert distinctive_slice not in _read(path), f"{name} appears to duplicate CLAUDE.md text verbatim"


def test_skill_files_reference_claude_md_rather_than_restating_it():
    for name, path in SKILLS.items():
        text = _read(path)
        assert "CLAUDE.md" in text, f"{name} should reference CLAUDE.md rather than silently assuming its rules"


def test_skill_files_contain_stop_and_approval_language():
    for name, path in SKILLS.items():
        text = _normalize(_read(path).lower())
        assert "stop" in text, f"{name} is missing explicit stop language"
        assert "explicit" in text, f"{name} is missing explicit-approval language"
        assert "approval" in text or "go-ahead" in text, f"{name} is missing approval/go-ahead language"


def test_resume_skill_forbids_editing_and_guessing_success():
    text = _read(SKILLS["forgeops-resume"]).lower()
    assert "never edit" in text or "never edits" in text
    assert "never infer success" in text or "without evidence" in text


def test_validate_skill_forbids_commit_push_and_mutating_other_repos():
    text = _normalize(_read(SKILLS["forgeops-validate"]).lower())
    assert "never commit" in text
    assert "never" in text and "push" in text
    assert "read-only reference repository" in text


def test_completion_report_skill_forbids_fabricated_evidence():
    text = _read(SKILLS["forgeops-completion-report"]).lower()
    assert "fabricate" in text or "not actually run" in text


# --- subagent: read-only, no mutation tools, no MCP, no TrendForge -------

def test_recovery_reviewer_agent_file_exists():
    assert AGENT_PATH.is_file()


def test_recovery_reviewer_stays_under_size_ceiling():
    size = len(_read(AGENT_PATH).encode("utf-8"))
    assert size <= AGENT_SIZE_CEILING_BYTES


def test_recovery_reviewer_tools_are_read_only_only():
    text = _read(AGENT_PATH)
    header = text.split("---", 2)[1]
    tools_line = next(line for line in header.splitlines() if line.strip().startswith("tools:"))
    declared_tools = {t.strip() for t in tools_line.split(":", 1)[1].split(",")}
    assert declared_tools == {"Read", "Grep", "Glob"}, f"unexpected tool grant: {declared_tools}"


def test_recovery_reviewer_declares_no_mutation_tools():
    text = _read(AGENT_PATH)
    header = text.split("---", 2)[1]
    tools_line = next(line for line in header.splitlines() if line.strip().startswith("tools:"))
    for forbidden in ("Bash", "Edit", "Write", "NotebookEdit", "Agent"):
        assert forbidden not in tools_line, f"recovery reviewer must not be granted {forbidden}"


def test_recovery_reviewer_never_declares_mcp_tools():
    text = _read(AGENT_PATH)
    assert "mcp__" not in text


def test_recovery_reviewer_forbids_trendforge_mutation():
    text = _normalize(_read(AGENT_PATH).lower())
    assert "trendforge" in text or "read-only reference repository" in text


def test_recovery_reviewer_defines_a_compact_output_contract():
    text = _read(AGENT_PATH).lower()
    assert "compact" in text
    assert "completed" in text and "missing" in text


def test_recovery_reviewer_forbids_implementation_and_mutation_actions():
    text = _read(AGENT_PATH).lower()
    for forbidden_action in ("never edit", "never commit", "never terminate", "never install", "never begin implementation"):
        assert forbidden_action in text, f"missing required restriction: {forbidden_action!r}"
