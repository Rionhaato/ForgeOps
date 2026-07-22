from __future__ import annotations

from pathlib import Path

from forgeops.security.secret_scan import scan_file, scan_text


def test_scan_text_detects_aws_key():
    findings = scan_text("key = AKIAABCDEFGHIJKLMNOP", "config.py")  # forgeops:allow-secret
    assert len(findings) == 1
    assert findings[0].category == "aws_access_key_id"
    assert findings[0].severity == "critical"


def test_scan_text_never_includes_matched_value():
    secret_value = "AKIAABCDEFGHIJKLMNOP"  # forgeops:allow-secret
    findings = scan_text(f"key = {secret_value}", "config.py")
    assert secret_value not in findings[0].redacted_match
    assert secret_value not in repr(findings[0])


def test_scan_text_detects_openai_style_key():
    findings = scan_text("OPENAI_KEY = sk-" + "a" * 40, "app.py")  # forgeops:allow-secret
    assert any(f.category == "openai_style_key" for f in findings)


def test_scan_text_detects_private_key_block():
    findings = scan_text("-----BEGIN RSA PRIVATE KEY-----\nMIIB...\n", "id_rsa.txt")  # forgeops:allow-secret
    assert any(f.category == "private_key_block" for f in findings)


def test_scan_text_clean_file_has_no_findings():
    findings = scan_text("just some ordinary source code\nx = 1 + 2\n", "app.py")
    assert findings == []


def test_scan_text_line_numbers_are_1_indexed():
    text = "line one\nline two\nkey = AKIAABCDEFGHIJKLMNOP\n"  # forgeops:allow-secret
    findings = scan_text(text, "f.py")
    assert findings[0].line == 3


def test_scan_file_skips_binary_suffix(tmp_path: Path):
    path = tmp_path / "image.png"
    path.write_bytes(b"AKIAABCDEFGHIJKLMNOP" * 5)  # forgeops:allow-secret
    assert scan_file(tmp_path, "image.png", max_bytes=10_000) == []


def test_scan_file_skips_oversized_file(tmp_path: Path):
    path = tmp_path / "big.txt"
    path.write_text("AKIAABCDEFGHIJKLMNOP\n" * 1000, encoding="utf-8")  # forgeops:allow-secret
    assert scan_file(tmp_path, "big.txt", max_bytes=10) == []


def test_scan_file_finds_secret_in_text_file(tmp_path: Path):
    path = tmp_path / "notes.txt"
    path.write_text("AKIAABCDEFGHIJKLMNOP\n", encoding="utf-8")  # forgeops:allow-secret
    findings = scan_file(tmp_path, "notes.txt", max_bytes=10_000)
    assert len(findings) == 1
    assert findings[0].file == "notes.txt"


def test_allowlist_marker_suppresses_the_line():
    findings = scan_text("key = AKIAABCDEFGHIJKLMNOP  # forgeops:allow-secret", "config.py")
    assert findings == []


def test_allowlist_marker_only_suppresses_the_marked_line():
    text = "key = AKIAABCDEFGHIJKLMNOP  # forgeops:allow-secret\nkey2 = AKIAZZZZZZZZZZZZZZZZ\n"
    findings = scan_text(text, "config.py")
    assert len(findings) == 1
    assert findings[0].line == 2
