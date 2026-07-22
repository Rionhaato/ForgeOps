"""Secret-pattern scanning. Generalized and expanded from TrendForge's
scripts/forgeops/scan_secret_patterns.py (see docs/phase2a-porting-notes.md).
Hard rule, stricter than the original: the matched substring is never
returned, stored, or printed anywhere - only a category label, file, and
line number. Callers are responsible for not passing this module the
contents of files already classified as credential-bearing by category
(.env*, browser storage-state, etc.) - see forgeops/detectors/sensitive.py."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PATTERNS: dict[str, re.Pattern[str]] = {
    "aws_access_key_id": re.compile(r"AKIA[0-9A-Z]{16}"),
    "openai_style_key": re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "generic_bearer_token": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-_.~+/]{20,}=*"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "db_connection_string": re.compile(r"(?i)\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://\S+"),
}

SEVERITY: dict[str, str] = {
    "aws_access_key_id": "critical",
    "openai_style_key": "critical",
    "google_api_key": "critical",
    "private_key_block": "critical",
    "generic_bearer_token": "high",
    "jwt": "medium",
    "db_connection_string": "high",
}

REMEDIATION: dict[str, str] = {
    "aws_access_key_id": "Rotate the AWS credential immediately; remove it from git history if committed.",
    "openai_style_key": "Rotate the API key immediately; remove it from git history if committed.",
    "google_api_key": "Rotate the API key immediately; remove it from git history if committed.",
    "private_key_block": "Rotate the key pair; remove the private key from git history if committed.",
    "generic_bearer_token": "Rotate the token; confirm it is not a long-lived credential checked in by mistake.",
    "jwt": "Confirm this is not a live session/auth token; rotate the underlying credential if so.",
    "db_connection_string": "Rotate the database credential; move the connection string to an environment variable.",
}

# Files unlikely to be useful/safe to scan as text - avoids binary noise.
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".mp4", ".mov", ".webm", ".mp3",
    ".wav", ".onnx", ".gguf", ".safetensors", ".pt", ".pth", ".db", ".sqlite",
    ".sqlite3", ".pdf", ".zip", ".exe", ".dll", ".so", ".dylib",
}


@dataclass(frozen=True)
class SecretFinding:
    category: str
    file: str
    line: int | None
    redacted_match: str
    severity: str
    remediation: str


ALLOWLIST_MARKER = "forgeops:allow-secret"


def scan_text(text: str, relative_path: str) -> list[SecretFinding]:
    """Scan `text` line by line. A line containing the literal marker
    `forgeops:allow-secret` is skipped entirely - the intended use is
    fixture/test files that deliberately contain fake, secret-shaped
    strings to exercise this scanner itself (this project's own tests do
    exactly that). The marker is a plain substring check, not a comment
    syntax, so it works in any file type."""
    findings: list[SecretFinding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if ALLOWLIST_MARKER in line:
            continue
        for category, pattern in PATTERNS.items():
            if pattern.search(line):
                findings.append(
                    SecretFinding(
                        category=category,
                        file=relative_path,
                        line=lineno,
                        redacted_match=f"<{category} pattern matched, value redacted>",
                        severity=SEVERITY[category],
                        remediation=REMEDIATION[category],
                    )
                )
    return findings


def scan_file(repo_root: Path, relative_path: str, max_bytes: int) -> list[SecretFinding]:
    path = repo_root / relative_path
    if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
        return []
    try:
        if path.stat().st_size > max_bytes:
            return []
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return scan_text(text, relative_path)
