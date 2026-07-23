"""SPEC.md/RESULT.md rendering and parsing, and safe ingestion of a
user-supplied `--spec-file`/`--acceptance`/`--result-file` for the Task
Specification Engine (`forgeops task create`/`forgeops task close`). See
docs/tasks.md for the section contract this implements.

Every "read a local file" helper here only ever reads - existence,
directory, and size checks happen before any content is trusted, and
the content is never executed or interpreted as commands, only stored
as text. Secret-shaped content detection happens in the caller
(`forgeops.state.task_create`/`task_close`), via
`forgeops.security.secret_scan.scan_text` - this module only handles
format/shape, not secret detection."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REQUIRED_SPEC_HEADINGS: tuple[str, ...] = (
    "Objective",
    "In Scope",
    "Out of Scope",
    "Constraints",
    "Acceptance Criteria",
    "Required Validation",
    "Stop Boundary",
)

REQUIRED_RESULT_HEADINGS: tuple[str, ...] = (
    "Outcome",
    "Changes",
    "Validation Evidence",
    "Remaining Blockers",
    "Follow-up",
    "Approval Notes",
)

_DEFAULT_ACCEPTANCE_PLACEHOLDER = "(not yet defined - add explicit, checkable criteria before this task leaves `draft`)"
_DEFAULT_REQUIRED_VALIDATION_PLACEHOLDER = "(not yet defined - list the exact commands/checks that must pass before this task can close)"

# Extensions this checkpoint refuses for `--acceptance FILE` - "do not
# accept executable scripts in this checkpoint" (SPEC.md content itself
# is never executed either way; this is a defense-in-depth format gate
# specific to the acceptance-criteria import path).
_SCRIPT_EXTENSIONS = frozenset({
    ".sh", ".bash", ".ps1", ".psm1", ".bat", ".cmd", ".exe", ".dll",
    ".py", ".js", ".mjs", ".cjs", ".rb", ".pl", ".php", ".vbs",
})


@dataclass(frozen=True)
class SourceFileRead:
    ok: bool
    content: str | None
    error: str | None


def read_source_file(path: Path, max_bytes: int) -> SourceFileRead:
    """Read a local file the caller supplied via `--spec-file`,
    `--acceptance`, or `--result-file`. Never raises - every failure mode
    (missing, directory, oversized, unreadable/binary) is reported as a
    concise error string, never a traceback."""
    if not path.exists():
        return SourceFileRead(ok=False, content=None, error=f"file does not exist: {path}")
    if path.is_dir():
        return SourceFileRead(ok=False, content=None, error=f"path is a directory, not a file: {path}")
    if not path.is_file():
        return SourceFileRead(ok=False, content=None, error=f"path is not a regular file: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        return SourceFileRead(ok=False, content=None, error=f"could not stat file: {exc}")
    if size > max_bytes:
        return SourceFileRead(ok=False, content=None, error=f"file is {size} bytes, exceeding the {max_bytes}-byte limit")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return SourceFileRead(ok=False, content=None, error=f"could not read file as UTF-8 text: {exc}")
    return SourceFileRead(ok=True, content=content, error=None)


def is_script_like(path: Path, content: str) -> bool:
    """True if `path`/`content` looks like an executable script rather
    than plain acceptance-criteria text or data - a shebang line, or a
    recognized script/executable extension."""
    if path.suffix.lower() in _SCRIPT_EXTENSIONS:
        return True
    stripped = content.lstrip()
    return stripped.startswith("#!")


@dataclass(frozen=True)
class AcceptanceParseResult:
    ok: bool
    criteria: list[str]
    error: str | None


def parse_acceptance_content(path: Path, content: str) -> AcceptanceParseResult:
    """Parse `--acceptance FILE` content into a flat list of concise
    criteria lines. `.json` files must be a JSON array of strings, or a
    JSON object with a `criteria` array of strings; anything else is
    treated as plain text, one non-empty line per criterion."""
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            return AcceptanceParseResult(ok=False, criteria=[], error=f"acceptance file is not valid JSON: {exc}")
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict) and isinstance(data.get("criteria"), list):
            items = data["criteria"]
        else:
            return AcceptanceParseResult(
                ok=False, criteria=[],
                error="acceptance JSON must be an array of strings, or an object with a 'criteria' array of strings",
            )
        if not all(isinstance(item, str) for item in items):
            return AcceptanceParseResult(ok=False, criteria=[], error="every acceptance criterion must be a string")
        criteria = [item.strip() for item in items if item.strip()]
        if not criteria:
            return AcceptanceParseResult(ok=False, criteria=[], error="acceptance file contains no non-empty criteria")
        return AcceptanceParseResult(ok=True, criteria=criteria, error=None)

    lines = [line.strip().lstrip("-*").strip() for line in content.splitlines()]
    criteria = [line for line in lines if line]
    if not criteria:
        return AcceptanceParseResult(ok=False, criteria=[], error="acceptance file contains no non-empty lines")
    return AcceptanceParseResult(ok=True, criteria=criteria, error=None)


def render_spec_md(title: str, acceptance_criteria: list[str] | None) -> str:
    """The default, minimal SPEC.md template for a freshly created task -
    every required section present but requiring later human completion,
    never a large implementation plan inferred from `title`."""
    acceptance_body = (
        "\n".join(f"- {c}" for c in acceptance_criteria)
        if acceptance_criteria else _DEFAULT_ACCEPTANCE_PLACEHOLDER
    )
    return (
        f"# SPEC: {title}\n\n"
        "## Objective\n\n"
        "(not yet defined - describe what this task must accomplish)\n\n"
        "## In Scope\n\n"
        "(not yet defined)\n\n"
        "## Out of Scope\n\n"
        "(not yet defined)\n\n"
        "## Constraints\n\n"
        "See `CLAUDE.md` and ForgeOps governance "
        "(`.agent/CURRENT_STATE.json`, `.agent/DECISIONS.md`) for durable "
        "operating rules that apply to every task in this project - not "
        "duplicated here.\n\n"
        "## Acceptance Criteria\n\n"
        f"{acceptance_body}\n\n"
        "## Required Validation\n\n"
        f"{_DEFAULT_REQUIRED_VALIDATION_PLACEHOLDER}\n\n"
        "## Stop Boundary\n\n"
        "(not yet defined - state explicitly what this task must NOT do)\n"
    )


def append_imported_acceptance_section(spec_content: str, acceptance_criteria: list[str]) -> str:
    """Appends an additional acceptance-criteria section to a
    user-supplied `--spec-file`'s content, used only when both
    `--spec-file` and `--acceptance` are given together - a custom
    spec file's own structure is never guessed at or rewritten in
    place."""
    body = "\n".join(f"- {c}" for c in acceptance_criteria)
    suffix = "" if spec_content.endswith("\n") else "\n"
    return f"{spec_content}{suffix}\n## Acceptance Criteria (imported)\n\n{body}\n"


def parse_markdown_sections(text: str, expected_headings: tuple[str, ...]) -> dict[str, str]:
    """Split `text` on `## <Heading>` markers into {heading: body}. Only
    headings from `expected_headings` are recognized; any other `##`
    heading present is ignored for this purpose (a supplied spec/result
    file may have extra sections of its own)."""
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []

    def _flush() -> None:
        if current is not None:
            sections[current] = "\n".join(buffer).strip()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            heading = stripped[3:].strip()
            if heading in expected_headings:
                _flush()
                current = heading
                buffer = []
                continue
            if current is not None:
                # A different, non-required heading ends the current one.
                _flush()
                current = None
                buffer = []
                continue
        if current is not None:
            buffer.append(line)
    _flush()
    return sections


def missing_required_headings(text: str, required: tuple[str, ...]) -> list[str]:
    present = {line.strip()[3:].strip() for line in text.splitlines() if line.strip().startswith("## ")}
    return [h for h in required if h not in present]


def is_placeholder_section(body: str) -> bool:
    """True if a required SPEC.md section still holds only the default
    placeholder text (or is empty) - used to distinguish "not yet
    defined" from genuine content."""
    stripped = body.strip()
    return not stripped or stripped.startswith("(not yet defined")
