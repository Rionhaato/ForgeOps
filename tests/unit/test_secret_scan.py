from __future__ import annotations

from pathlib import Path

import pytest

from forgeops.security.secret_scan import scan_file, scan_text


def test_scan_text_detects_aws_key():
    findings, exemptions = scan_text("key = AKIAABCDEFGHIJKLMNOP", "config.py")  # forgeops:allow-secret
    assert len(findings) == 1
    assert exemptions == []
    assert findings[0].category == "aws_access_key_id"
    assert findings[0].severity == "critical"


def test_scan_text_never_includes_matched_value():
    secret_value = "AKIAABCDEFGHIJKLMNOP"  # forgeops:allow-secret
    findings, _exemptions = scan_text(f"key = {secret_value}", "config.py")
    assert secret_value not in findings[0].redacted_match
    assert secret_value not in repr(findings[0])


def test_scan_text_detects_openai_style_key():
    findings, _exemptions = scan_text("OPENAI_KEY = sk-" + "a" * 40, "app.py")  # forgeops:allow-secret
    assert any(f.category == "openai_style_key" for f in findings)


def test_scan_text_detects_private_key_block():
    findings, _exemptions = scan_text("-----BEGIN RSA PRIVATE KEY-----\nMIIB...\n", "id_rsa.txt")  # forgeops:allow-secret
    assert any(f.category == "private_key_block" for f in findings)


def test_scan_text_clean_file_has_no_findings():
    findings, exemptions = scan_text("just some ordinary source code\nx = 1 + 2\n", "app.py")
    assert findings == []
    assert exemptions == []


def test_scan_text_line_numbers_are_1_indexed():
    text = "line one\nline two\nkey = AKIAABCDEFGHIJKLMNOP\n"  # forgeops:allow-secret
    findings, _exemptions = scan_text(text, "f.py")
    assert findings[0].line == 3


def test_scan_file_skips_binary_suffix(tmp_path: Path):
    path = tmp_path / "image.png"
    path.write_bytes(b"AKIAABCDEFGHIJKLMNOP" * 5)  # forgeops:allow-secret
    assert scan_file(tmp_path, "image.png", max_bytes=10_000) == ([], [])


def test_scan_file_skips_oversized_file(tmp_path: Path):
    path = tmp_path / "big.txt"
    path.write_text("AKIAABCDEFGHIJKLMNOP\n" * 1000, encoding="utf-8")  # forgeops:allow-secret
    assert scan_file(tmp_path, "big.txt", max_bytes=10) == ([], [])


def test_scan_file_finds_secret_in_text_file(tmp_path: Path):
    path = tmp_path / "notes.txt"
    path.write_text("AKIAABCDEFGHIJKLMNOP\n", encoding="utf-8")  # forgeops:allow-secret
    findings, exemptions = scan_file(tmp_path, "notes.txt", max_bytes=10_000)
    assert len(findings) == 1
    assert exemptions == []
    assert findings[0].file == "notes.txt"


# --- allow-secret marker: scoping and adversarial tests (Phase 2B, Part 6) ---

def test_allowlist_marker_suppresses_the_line_in_an_approved_fixture_path():
    findings, exemptions = scan_text(
        "key = AKIAABCDEFGHIJKLMNOP  # forgeops:allow-secret", "tests/fixtures/config.py"  # forgeops:allow-secret
    )
    assert findings == []
    assert len(exemptions) == 1
    assert exemptions[0].category == "aws_access_key_id"
    assert exemptions[0].file == "tests/fixtures/config.py"


def test_allowlist_marker_only_suppresses_the_marked_line():
    text = (
        "key = AKIAABCDEFGHIJKLMNOP  # forgeops:allow-secret\n"  # forgeops:allow-secret
        "key2 = AKIAZZZZZZZZZZZZZZZZ\n"  # forgeops:allow-secret
    )
    findings, exemptions = scan_text(text, "tests/fixtures/config.py")
    assert len(findings) == 1
    assert findings[0].line == 2
    assert len(exemptions) == 1
    assert exemptions[0].line == 1


def test_allowlist_marker_does_not_suppress_in_production_source_path():
    """The mission-critical guarantee: normal application source cannot
    bypass scanning merely by adding the marker. "backend/config.py" is
    not a tests/fixtures/examples path, so the marker must be inert."""
    findings, exemptions = scan_text(
        "key = AKIAABCDEFGHIJKLMNOP  # forgeops:allow-secret", "backend/config.py"  # forgeops:allow-secret
    )
    assert len(findings) == 1
    assert exemptions == []


def test_allowlist_marker_does_not_suppress_at_repo_root_source_file():
    findings, exemptions = scan_text(
        "key = AKIAABCDEFGHIJKLMNOP  # forgeops:allow-secret", "app.py"  # forgeops:allow-secret
    )
    assert len(findings) == 1
    assert exemptions == []


def test_allowlist_marker_does_not_suppress_credential_bearing_url_in_production_source():
    findings, exemptions = scan_text(
        "DATABASE_URL = 'postgres://user:pw@host/db'  # forgeops:allow-secret", "backend/app/settings.py"  # forgeops:allow-secret
    )
    assert any(f.category == "db_connection_string" for f in findings)
    assert exemptions == []


def test_extra_allow_patterns_extend_the_approved_zone():
    findings, exemptions = scan_text(
        "key = AKIAABCDEFGHIJKLMNOP  # forgeops:allow-secret", "tools/fake-creds/sample.py",  # forgeops:allow-secret
        extra_allow_patterns=("tools/fake-creds/**",),
    )
    assert findings == []
    assert len(exemptions) == 1


def test_env_file_never_scanned_regardless_of_marker(tmp_path: Path):
    """.env files cannot bypass scanning via the marker - they are never
    opened at all, categorical-exclusion, independent of marker logic."""
    path = tmp_path / ".env"
    path.write_text("SECRET_KEY=AKIAABCDEFGHIJKLMNOP  # forgeops:allow-secret\n", encoding="utf-8")  # forgeops:allow-secret
    findings, exemptions = scan_file(tmp_path, ".env", max_bytes=10_000)
    assert findings == []
    assert exemptions == []


def test_env_example_also_never_opened(tmp_path: Path):
    path = tmp_path / "backend" / ".env.example"
    path.parent.mkdir(parents=True)
    path.write_text("SECRET_KEY=AKIAABCDEFGHIJKLMNOP\n", encoding="utf-8")  # forgeops:allow-secret
    findings, exemptions = scan_file(tmp_path, "backend/.env.example", max_bytes=10_000)
    assert findings == []
    assert exemptions == []


def test_browser_state_file_never_scanned_regardless_of_marker(tmp_path: Path):
    path = tmp_path / "tests" / "auth-state" / "qa-user.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"token": "AKIAABCDEFGHIJKLMNOP"}  // forgeops:allow-secret\n', encoding="utf-8")  # forgeops:allow-secret
    findings, exemptions = scan_file(tmp_path, "tests/auth-state/qa-user.json", max_bytes=10_000)
    assert findings == []
    assert exemptions == []


def test_database_file_never_scanned_regardless_of_marker(tmp_path: Path):
    path = tmp_path / "tests" / "fixtures" / "app.sqlite3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"AKIAABCDEFGHIJKLMNOP forgeops:allow-secret")  # forgeops:allow-secret
    findings, exemptions = scan_file(tmp_path, "tests/fixtures/app.sqlite3", max_bytes=10_000)
    assert findings == []
    assert exemptions == []


# --- this repository's own synthetic fixtures are exempted, not merely absent ---
#
# Several suites feed deliberately secret-shaped strings into ForgeOps to
# prove it *refuses* or *redacts* them. Those inputs must stay
# secret-shaped to be worth anything, so each is annotated with an inline
# marker. The tests below assert the resulting behavior - zero findings,
# a matching exemption record - rather than the presence of marker text,
# so they fail in both directions: removing a marker produces a finding,
# and deleting/defanging the fixture value makes its exemption disappear.

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_MAX_BYTES = 2_000_000

# relative path -> (number of marked fixture lines, detector categories exercised)
MARKED_FIXTURE_FILES: dict[str, tuple[int, set[str]]] = {
    "tests/unit/test_agent_register.py": (1, {"aws_access_key_id"}),
    "tests/unit/test_processes.py": (1, {"generic_bearer_token"}),
    "tests/unit/test_resume_context.py": (2, {"generic_bearer_token"}),
    "tests/unit/test_task_approval.py": (2, {"aws_access_key_id"}),
    "tests/unit/test_task_close.py": (1, {"aws_access_key_id"}),
    "tests/unit/test_task_create.py": (3, {"aws_access_key_id"}),
    "tests/unit/test_task_validate.py": (2, {"aws_access_key_id"}),
    "tests/integration/test_cli_agent.py": (2, {"aws_access_key_id", "openai_style_key"}),
    "tests/integration/test_cli_cleanup.py": (1, {"generic_bearer_token"}),
    "tests/integration/test_cli_process_list.py": (1, {"generic_bearer_token"}),
    "tests/integration/test_cli_task.py": (2, {"aws_access_key_id"}),
    "tests/integration/test_cli_task_approval.py": (2, {"aws_access_key_id"}),
}


@pytest.mark.parametrize("relative_path", sorted(MARKED_FIXTURE_FILES))
def test_marked_fixture_file_yields_exemptions_and_no_findings(relative_path):
    expected_count, expected_categories = MARKED_FIXTURE_FILES[relative_path]
    findings, exemptions = scan_file(REPO_ROOT, relative_path, max_bytes=SCAN_MAX_BYTES)
    assert findings == [], f"{relative_path} has an unexempted secret finding"
    assert len(exemptions) == expected_count, (
        f"{relative_path} recorded {len(exemptions)} exemption(s), expected {expected_count} - "
        "a fixture value was removed, defanged, or newly added without review"
    )
    assert {e.category for e in exemptions} == expected_categories


def test_no_test_file_in_the_repository_has_an_unexempted_secret_finding():
    """The end-to-end invariant `forgeops audit` depends on: every
    secret-shaped string under tests/ is either absent or explicitly
    exempted - never silently blocking the release gate."""
    offenders = []
    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        findings, _exemptions = scan_file(REPO_ROOT, relative, max_bytes=SCAN_MAX_BYTES)
        offenders.extend((f.file, f.line, f.category) for f in findings)
    assert offenders == []


def test_marked_fixture_lines_still_contain_a_matching_secret_shaped_value():
    """An exemption is only recorded when a pattern actually matched, so
    the total across the annotated files proves the fixtures still
    exercise the detectors they were written for."""
    total = 0
    for relative_path in MARKED_FIXTURE_FILES:
        _findings, exemptions = scan_file(REPO_ROOT, relative_path, max_bytes=SCAN_MAX_BYTES)
        total += len(exemptions)
    assert total == sum(count for count, _categories in MARKED_FIXTURE_FILES.values())


# --- the exemption remains narrow: zone alone is not enough --------------------

SYNTHETIC_AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
MARKER = "forgeops:allow-secret"


def test_unmarked_synthetic_value_in_a_test_path_is_still_a_finding():
    """Being inside tests/ grants *eligibility* for an exemption, never
    the exemption itself - a new unmarked fixture must still block."""
    findings, exemptions = scan_text(f"key = {SYNTHETIC_AWS_KEY}", "tests/unit/test_new_fixture.py")
    assert len(findings) == 1
    assert findings[0].category == "aws_access_key_id"
    assert exemptions == []


@pytest.mark.parametrize(
    "near_miss",
    [
        "forgeops:allow_secret",
        "forgeops-allow-secret",
        "allow-secret",
        "FORGEOPS:ALLOW-SECRET",
        "forgeops: allow-secret",
    ],
)
def test_malformed_marker_spelling_does_not_exempt(near_miss):
    findings, exemptions = scan_text(
        f"key = {SYNTHETIC_AWS_KEY}  # {near_miss}", "tests/unit/test_fixture.py",
    )
    assert len(findings) == 1
    assert exemptions == []


def test_file_header_marker_does_not_exempt_the_rest_of_the_file():
    """The realistic file-wide bypass attempt: one marker at the top of a
    file, hoping it covers everything below. Exemption is per-line."""
    text = f"# {MARKER}\nimport os\nkey = {SYNTHETIC_AWS_KEY}\n"
    findings, exemptions = scan_text(text, "tests/unit/test_fixture.py")
    assert len(findings) == 1
    assert findings[0].line == 3
    assert exemptions == []


def test_marker_in_a_sibling_file_does_not_exempt_a_directory():
    """Eligibility is computed per path and exemption per line, so a
    marked file grants nothing to its unmarked neighbour."""
    marked, marked_exemptions = scan_text(
        f"key = {SYNTHETIC_AWS_KEY}  # {MARKER}", "tests/fixtures/marked.py",
    )
    neighbour, neighbour_exemptions = scan_text(
        f"key = {SYNTHETIC_AWS_KEY}", "tests/fixtures/neighbour.py",
    )
    assert marked == [] and len(marked_exemptions) == 1
    assert len(neighbour) == 1 and neighbour_exemptions == []


# --- the marker is a source-scanning concept, never a runtime bypass ----------


@pytest.mark.parametrize(
    "runtime_path",
    [
        ".agent/tasks/task-0001/__actor__",
        ".agent/tasks/task-0001/__approval_reason__",
        ".agent/tasks/__pending__/--spec-file",
        ".agent/agents/__pending_display_name__",
        ".agent/tasks/task-0001/SPEC.md",
        ".agent/tasks/task-0001/TASK.json",
    ],
)
def test_marker_is_inert_on_the_synthetic_paths_runtime_ingestion_uses(runtime_path):
    """`forgeops.state.*` scans user-supplied actors, reasons, spec files
    and result files under synthetic `.agent/...` paths precisely so they
    fall outside every approved zone. A user who embeds the marker in
    their own input must still be refused."""
    findings, exemptions = scan_text(f"key is {SYNTHETIC_AWS_KEY}  # {MARKER}", runtime_path)
    assert len(findings) == 1
    assert exemptions == []
