"""Secret-pattern scanning. Generalized and expanded from TrendForge's
scripts/forgeops/scan_secret_patterns.py (see docs/phase2a-porting-notes.md).
Hard rule, stricter than the original: the matched substring is never
returned, stored, or printed anywhere - only a category label, file, and
line number. Callers are responsible for not passing this module the
contents of files already classified as credential-bearing by category
(.env*, browser storage-state, etc.) - see forgeops/detectors/tree_scan.py.
This module additionally refuses to open a categorically credential-
bearing path itself (see _is_categorically_excluded), as a second,
independent layer rather than relying solely on callers doing the right
thing.

## The forgeops:allow-secret marker (see docs/audit-security-model.md)

A line containing the literal substring `forgeops:allow-secret` is only
exempted from a finding when the file's path also matches an approved
fixture/test zone (tests/, fixtures/, examples/, common test-naming
conventions, or an explicitly configured `allow_secret_paths` entry).
Outside those zones the marker is inert: the line is scanned normally,
so normal application source cannot self-declare an exemption. Every
exemption that *is* granted is still recorded (as a SecretExemption,
category/file/line only - never the matched value) so it shows up in
audit output as an informational finding rather than vanishing silently."""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path

from forgeops.detectors.tree_scan import BROWSER_STATE_NAME_HINTS, DB_SUFFIXES, ENV_FILE_RE, ENV_SAFE_RE

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

# Default zones where the allow-secret marker is honored. Deliberately
# narrow: test directories, fixture directories, example directories, and
# common test-file naming conventions - never a wildcard, never "anything
# under src/". Projects can add more via config's allow_secret_paths.
DEFAULT_ALLOWLIST_PATH_PATTERNS: tuple[str, ...] = (
    "tests/*", "tests/**", "test/*", "test/**",
    "*/tests/*", "*/tests/**", "*/test/*", "*/test/**",
    "**/tests/**", "**/test/**",
    "**/fixtures/**", "**/fixture/**",
    "examples/*", "examples/**", "**/examples/**",
    "**/test_*.py", "**/*_test.py",
    "**/*.test.js", "**/*.test.ts", "**/*.test.jsx", "**/*.test.tsx",
    "**/*.spec.js", "**/*.spec.ts", "**/*.spec.jsx", "**/*.spec.tsx",
)


@dataclass(frozen=True)
class SecretFinding:
    category: str
    file: str
    line: int | None
    redacted_match: str
    severity: str
    remediation: str


@dataclass(frozen=True)
class SecretExemption:
    category: str
    file: str
    line: int
    reason: str


ALLOWLIST_MARKER = "forgeops:allow-secret"


def _is_categorically_excluded(relative_path: str) -> bool:
    """True for paths this module refuses to open at all, regardless of
    any marker: env files, database files, browser/session state files.
    This is a second, independent layer of the same rule tree_scan.py
    already enforces by never adding these paths to scannable_text_files -
    belt and suspenders, so a caller that (by mistake or in the future)
    passes one of these paths directly still can't have it scanned."""
    name = relative_path.rsplit("/", 1)[-1]
    if ENV_SAFE_RE.match(name) or ENV_FILE_RE.match(name):
        return True
    suffix = Path(name).suffix.lower()
    if suffix in DB_SUFFIXES:
        return True
    if any(hint in relative_path.lower() for hint in BROWSER_STATE_NAME_HINTS):
        return True
    return False


def _path_is_exemption_eligible(relative_path: str, extra_allow_patterns: tuple[str, ...]) -> bool:
    normalized = relative_path.replace("\\", "/")
    for pattern in (*DEFAULT_ALLOWLIST_PATH_PATTERNS, *extra_allow_patterns):
        if fnmatch.fnmatch(normalized, pattern):
            return True
    return False


def scan_text(
    text: str,
    relative_path: str,
    extra_allow_patterns: tuple[str, ...] = (),
) -> tuple[list[SecretFinding], list[SecretExemption]]:
    """Scan `text` line by line. Returns (findings, exemptions) - never a
    single flat list - so a caller can never accidentally treat a granted
    exemption as if nothing happened; exemptions must be surfaced too."""
    findings: list[SecretFinding] = []
    exemptions: list[SecretExemption] = []
    eligible = _path_is_exemption_eligible(relative_path, extra_allow_patterns)

    for lineno, line in enumerate(text.splitlines(), start=1):
        marker_present = ALLOWLIST_MARKER in line
        for category, pattern in PATTERNS.items():
            if not pattern.search(line):
                continue
            if marker_present and eligible:
                exemptions.append(
                    SecretExemption(
                        category=category,
                        file=relative_path,
                        line=lineno,
                        reason=f"{ALLOWLIST_MARKER} marker in an approved fixture/test path",
                    )
                )
                continue
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
    return findings, exemptions


def scan_file(
    repo_root: Path,
    relative_path: str,
    max_bytes: int,
    extra_allow_patterns: tuple[str, ...] = (),
) -> tuple[list[SecretFinding], list[SecretExemption]]:
    if _is_categorically_excluded(relative_path):
        return [], []
    path = repo_root / relative_path
    if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
        return [], []
    try:
        if path.stat().st_size > max_bytes:
            return [], []
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return [], []
    return scan_text(text, relative_path, extra_allow_patterns)
