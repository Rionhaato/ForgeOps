from __future__ import annotations

from pathlib import Path

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
