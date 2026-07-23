"""Small constants mirroring specific `CLAUDE.md` sections, shared by any
module that needs to state them (currently `forgeops handoff` and
`forgeops resume-context`) without duplicating the tuple in each, and
without either module depending on the other. Deliberately not parsed
out of `CLAUDE.md` at runtime - see the Phase 2C decision in
`.agent/DECISIONS.md` ("approval boundaries and standard validation
commands mirrored as constants, not re-derived from CLAUDE.md")."""
from __future__ import annotations

# Mirrors CLAUDE.md section 8 ("Standard validation commands").
STANDARD_VALIDATION_COMMANDS = (
    "python -m pytest tests -q",
    "python -m compileall -q forgeops tests",
    "python -m forgeops doctor",
    "python -m forgeops status",
    "python -m forgeops audit",
    "git diff --check",
    "git status --short",
)

# Mirrors CLAUDE.md section 11 ("Approval boundaries").
APPROVAL_BOUNDARY_CATEGORIES = (
    "installation",
    "authentication",
    "secrets access",
    "destructive actions",
    "production/publishing/spending/deployment/financial actions",
    "commits (unless explicitly authorized for this checkpoint)",
    "genuine ambiguity about sensitive files",
)

PROHIBITED_ACTIONS = (
    "Do not push, deploy, publish, or configure a remote.",
    "Do not force-push, `git reset --hard`, or rewrite history without explicit operator approval.",
    "Do not amend, squash, or rebase an existing commit.",
    "Do not install dependencies or authenticate an external service.",
    "Do not invoke any command against the read-only reference repository, if one is configured (see below).",
)
