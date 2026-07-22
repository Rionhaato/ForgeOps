"""Guards the always-loaded CLAUDE.md: it must exist, stay small, contain
the required sections, point at the state files, and never contain an
obvious credential pattern (this file is committed and always loaded, so
a real secret here would be maximally exposed)."""
from __future__ import annotations

from pathlib import Path

from forgeops.security.secret_scan import scan_text

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
MAX_LINES = 150

REQUIRED_SECTIONS = [
    "Project identity",
    "Purpose",
    "Session startup order",
    "Sources of truth",
    "Architecture rules",
    "Safety boundaries",
    "Development workflow",
    "Standard validation commands",
    "State and handoff rules",
    "Delegation rules",
    "Approval boundaries",
    "Definition of done",
]


def _text() -> str:
    return CLAUDE_MD.read_text(encoding="utf-8")


def test_claude_md_exists():
    assert CLAUDE_MD.is_file()


def test_claude_md_contains_every_required_section():
    text = _text()
    missing = [s for s in REQUIRED_SECTIONS if s not in text]
    assert missing == [], f"CLAUDE.md is missing required sections: {missing}"


def test_claude_md_references_current_state_and_handoff():
    text = _text()
    assert "CURRENT_STATE.json" in text
    assert "HANDOFF.md" in text


def test_claude_md_references_decisions_log():
    assert "DECISIONS.md" in _text()


def test_claude_md_stays_under_size_limit():
    line_count = len(_text().splitlines())
    assert line_count <= MAX_LINES, f"CLAUDE.md is {line_count} lines, over the {MAX_LINES}-line target"


def test_claude_md_has_no_obvious_secret_patterns():
    findings, exemptions = scan_text(_text(), "CLAUDE.md")
    assert findings == []
    assert exemptions == []


def test_claude_md_does_not_reference_a_volatile_commit_hash():
    import re
    # A bare 7-40 char hex token outside a fenced code block would read as
    # a pinned commit sha, which goes stale immediately in an always-loaded file.
    text = _text()
    code_fence_free = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    hex_tokens = re.findall(r"\b[0-9a-f]{7,40}\b", code_fence_free)
    assert hex_tokens == [], f"CLAUDE.md appears to reference commit-hash-shaped tokens: {hex_tokens}"
