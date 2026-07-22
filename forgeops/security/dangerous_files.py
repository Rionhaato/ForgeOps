"""Dangerous-filename pattern matching. Generalized from TrendForge's
scripts/forgeops/git_safety_check.py (see docs/phase2a-porting-notes.md):
the original hardcoded TrendForge paths (artifacts/presentation-demo/*)
are replaced with project-agnostic shape rules (env files, media files,
anything named credentials/secret)."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass

DANGEROUS_PATTERNS: list[str] = [
    "*.env",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*credentials*",
    "*secret*",
    "*.mp4",
    "*.mov",
]

# *credentials*/*secret* are name-shape heuristics meant to catch files
# likely to *contain* a credential (credentials.json, db-secret.txt).
# Source code that *implements* secret handling (forgeops/security/
# secret_scan.py, a consumer repo's own credentials_helper.js) matches the
# same shape without containing a value - actual embedded secret values in
# any file, any extension, are still caught separately by secret_scan.py's
# content-based scan, so excluding these extensions here doesn't weaken
# detection, it just removes a predictable false positive.
NAME_SHAPE_ONLY_PATTERNS = {"*credentials*", "*secret*"}
SOURCE_CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".rb"}

SAFE_EXCEPTIONS: list[str] = [
    "*.env.example",
]


@dataclass(frozen=True)
class DangerousMatch:
    path: str
    pattern: str


def match_dangerous(path: str) -> DangerousMatch | None:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    suffix = ("." + name.rsplit(".", 1)[-1]).lower() if "." in name else ""
    for safe_pattern in SAFE_EXCEPTIONS:
        if fnmatch.fnmatch(name, safe_pattern) or fnmatch.fnmatch(normalized, safe_pattern):
            return None
    for pattern in DANGEROUS_PATTERNS:
        if pattern in NAME_SHAPE_ONLY_PATTERNS and suffix in SOURCE_CODE_SUFFIXES:
            continue
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(normalized, pattern):
            return DangerousMatch(path=path, pattern=pattern)
    return None


def scan_paths(paths: list[str]) -> list[DangerousMatch]:
    matches = []
    for path in paths:
        match = match_dangerous(path)
        if match:
            matches.append(match)
    return matches
