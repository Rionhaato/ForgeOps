"""Tests for forgeops/state/task_spec.py: SPEC.md rendering/parsing and
safe ingestion of --spec-file/--acceptance/--result-file content."""
from __future__ import annotations

import json

from forgeops.state.task_spec import (
    REQUIRED_SPEC_HEADINGS,
    append_imported_acceptance_section,
    is_placeholder_section,
    is_script_like,
    missing_required_headings,
    parse_acceptance_content,
    parse_markdown_sections,
    read_source_file,
    render_spec_md,
)


# --- render_spec_md / required headings -------------------------------------


def test_render_spec_md_contains_all_required_headings():
    text = render_spec_md("My Task", None)
    assert not missing_required_headings(text, REQUIRED_SPEC_HEADINGS)


def test_render_spec_md_does_not_infer_a_plan_from_title():
    text = render_spec_md("Implement OAuth", None)
    assert "not yet defined" in text
    assert "OAuth" in text  # title itself is present, but nothing else invented


def test_render_spec_md_with_acceptance_criteria():
    text = render_spec_md("My Task", ["Criterion A", "Criterion B"])
    assert "Criterion A" in text
    assert "Criterion B" in text
    sections = parse_markdown_sections(text, REQUIRED_SPEC_HEADINGS)
    assert not is_placeholder_section(sections["Acceptance Criteria"])


def test_missing_required_headings_detects_absence():
    text = "# SPEC\n\n## Objective\n\nfoo\n"
    missing = missing_required_headings(text, REQUIRED_SPEC_HEADINGS)
    assert "Acceptance Criteria" in missing
    assert "Objective" not in missing


def test_parse_markdown_sections_extracts_body():
    text = "## Objective\n\nDo the thing.\n\n## In Scope\n\nJust this.\n"
    sections = parse_markdown_sections(text, REQUIRED_SPEC_HEADINGS)
    assert sections["Objective"] == "Do the thing."
    assert sections["In Scope"] == "Just this."


def test_is_placeholder_section():
    assert is_placeholder_section("") is True
    assert is_placeholder_section("(not yet defined)") is True
    assert is_placeholder_section("Real content here") is False


def test_append_imported_acceptance_section():
    base = "# Custom Spec\n\nfreeform content\n"
    result = append_imported_acceptance_section(base, ["A", "B"])
    assert "## Acceptance Criteria (imported)" in result
    assert "- A" in result
    assert "- B" in result
    assert "freeform content" in result


# --- read_source_file --------------------------------------------------------


def test_read_source_file_nonexistent(tmp_path):
    result = read_source_file(tmp_path / "nope.txt", max_bytes=1000)
    assert result.ok is False
    assert "does not exist" in result.error


def test_read_source_file_directory(tmp_path):
    d = tmp_path / "adir"
    d.mkdir()
    result = read_source_file(d, max_bytes=1000)
    assert result.ok is False
    assert "directory" in result.error


def test_read_source_file_oversized(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("x" * 100, encoding="utf-8")
    result = read_source_file(f, max_bytes=10)
    assert result.ok is False
    assert "exceeding" in result.error


def test_read_source_file_success(tmp_path):
    f = tmp_path / "spec.md"
    f.write_text("# hello\n", encoding="utf-8")
    result = read_source_file(f, max_bytes=1000)
    assert result.ok is True
    assert result.content == "# hello\n"


def test_read_source_file_never_executes_content(tmp_path):
    f = tmp_path / "evil.txt"
    f.write_text("$(rm -rf /)\n`id`\n", encoding="utf-8")
    result = read_source_file(f, max_bytes=1000)
    assert result.ok is True
    assert "$(rm -rf /)" in result.content  # preserved verbatim, never interpreted


# --- is_script_like -----------------------------------------------------------


def test_is_script_like_by_extension(tmp_path):
    f = tmp_path / "acceptance.sh"
    assert is_script_like(f, "echo hi") is True


def test_is_script_like_by_shebang(tmp_path):
    f = tmp_path / "acceptance.txt"
    assert is_script_like(f, "#!/bin/bash\necho hi") is True


def test_is_script_like_false_for_plain_text(tmp_path):
    f = tmp_path / "acceptance.txt"
    assert is_script_like(f, "- criterion one\n- criterion two\n") is False


# --- parse_acceptance_content --------------------------------------------------


def test_parse_acceptance_plain_text(tmp_path):
    f = tmp_path / "acceptance.txt"
    result = parse_acceptance_content(f, "- one\n- two\n\nthree\n")
    assert result.ok is True
    assert result.criteria == ["one", "two", "three"]


def test_parse_acceptance_plain_text_empty(tmp_path):
    f = tmp_path / "acceptance.txt"
    result = parse_acceptance_content(f, "   \n\n  \n")
    assert result.ok is False


def test_parse_acceptance_json_array(tmp_path):
    f = tmp_path / "acceptance.json"
    result = parse_acceptance_content(f, json.dumps(["a", "b"]))
    assert result.ok is True
    assert result.criteria == ["a", "b"]


def test_parse_acceptance_json_object_with_criteria(tmp_path):
    f = tmp_path / "acceptance.json"
    result = parse_acceptance_content(f, json.dumps({"criteria": ["a", "b"]}))
    assert result.ok is True
    assert result.criteria == ["a", "b"]


def test_parse_acceptance_json_invalid(tmp_path):
    f = tmp_path / "acceptance.json"
    result = parse_acceptance_content(f, "{ not valid")
    assert result.ok is False
    assert "not valid JSON" in result.error


def test_parse_acceptance_json_wrong_shape(tmp_path):
    f = tmp_path / "acceptance.json"
    result = parse_acceptance_content(f, json.dumps({"foo": "bar"}))
    assert result.ok is False


def test_parse_acceptance_json_non_string_items(tmp_path):
    f = tmp_path / "acceptance.json"
    result = parse_acceptance_content(f, json.dumps([1, 2, 3]))
    assert result.ok is False
