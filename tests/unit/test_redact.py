from __future__ import annotations

from forgeops.security.redact import redact_text


def test_redacts_url_credentials():
    text = "clone from https://myuser:sup3rSecret@example.invalid/repo.git please"  # forgeops:allow-secret
    out = redact_text(text)
    assert "sup3rSecret" not in out
    assert "myuser" not in out
    assert "[REDACTED]@example.invalid" in out


def test_redacts_db_connection_string():
    text = "DATABASE_URL points at postgres://user:pw@db.example.invalid:5432/app"  # forgeops:allow-secret
    out = redact_text(text)
    assert "pw@db.example.invalid" not in out
    assert "[REDACTED_DB_URL]" in out


def test_redacts_jwt():
    text = "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dGhpc2lzYWZha2VzaWc"  # forgeops:allow-secret
    out = redact_text(text)
    assert "eyJhbGciOiJIUzI1NiJ9" not in out
    assert "[REDACTED_JWT]" in out


def test_redacts_bearer_token():
    text = "Authorization header carried Bearer abcdefghijklmnopqrstuvwxyz0123456789"  # forgeops:allow-secret
    out = redact_text(text)
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in out
    assert "Bearer [REDACTED]" in out


def test_redacts_authorization_header_line():
    text = "Authorization: Bearer sometoken123456789\nother: line"
    out = redact_text(text)
    assert "sometoken123456789" not in out
    assert "other: line" in out


def test_redacts_cookie_header_line():
    text = "Cookie: session=abc123; other=xyz\nunrelated: line"
    out = redact_text(text)
    assert "abc123" not in out
    assert "unrelated: line" in out


def test_redacts_secret_shaped_env_assignment():
    text = "API_SECRET_KEY=abcd1234\nNORMAL_VALUE=hello"
    out = redact_text(text)
    assert "abcd1234" not in out
    assert "API_SECRET_KEY=[REDACTED]" in out
    assert "NORMAL_VALUE=hello" in out


def test_leaves_ordinary_text_untouched():
    text = "This is a perfectly ordinary log line with nothing sensitive in it."
    assert redact_text(text) == text
