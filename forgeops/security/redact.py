"""Text redaction applied to any raw command output before it is written
to logs/ or embedded in a CLI result. Pattern-based, not exhaustive - see
docs/audit-security-model.md for known limitations."""
from __future__ import annotations

import re

_URL_CREDENTIALS_RE = re.compile(r"(?P<scheme>\w+://)(?P<user>[^:/?#@\s]+):(?P<password>[^@/?#\s]+)@")
_DB_URL_RE = re.compile(r"(?i)\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s\"'<>]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-_.~+/]{10,}=*")
_AUTH_HEADER_RE = re.compile(r"(?im)^(Authorization|X-Api-Key|Api-Key)\s*:\s*.+$")
_COOKIE_HEADER_RE = re.compile(r"(?im)^(Cookie|Set-Cookie)\s*:\s*.+$")
_SECRET_ENV_ASSIGNMENT_RE = re.compile(
    r"(?im)^(?P<key>[A-Za-z_][A-Za-z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|PWD|CREDENTIAL)[A-Za-z0-9_]*)"
    r"\s*=\s*.*$"
)


def redact_text(text: str) -> str:
    """Apply every known redaction pattern to `text`, in an order chosen
    so more specific patterns (DB URLs, JWTs) run before the generic
    credentialed-URL pattern would otherwise partially match them."""
    text = _DB_URL_RE.sub("[REDACTED_DB_URL]", text)
    text = _JWT_RE.sub("[REDACTED_JWT]", text)
    text = _URL_CREDENTIALS_RE.sub(lambda m: f"{m.group('scheme')}[REDACTED]@", text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _AUTH_HEADER_RE.sub(lambda m: f"{m.group(1)}: [REDACTED]", text)
    text = _COOKIE_HEADER_RE.sub(lambda m: f"{m.group(1)}: [REDACTED]", text)
    text = _SECRET_ENV_ASSIGNMENT_RE.sub(lambda m: f"{m.group('key')}=[REDACTED]", text)
    return text
