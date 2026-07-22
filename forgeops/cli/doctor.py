"""`forgeops doctor` - environment/foundation health checks. Never mutates
the target repository; only ever writes inside its own logs/ directory
when a repository is found."""
from __future__ import annotations

import importlib
import platform
import shutil
import sys
from pathlib import Path

from forgeops.core import exit_codes
from forgeops.core.config import ConfigError, load_config
from forgeops.core.git import git_version
from forgeops.core.paths import RepoNotFoundError, resolve_repo_root
from forgeops.core.result import Check, CommandResult
from forgeops.core.timestamps import Clock, iso_now
from forgeops.reporting.logs import LogWriter

SCHEMA_VERSION = 1
MIN_PYTHON = (3, 11)
CONFIG_CHECK_IDS = {"config-validity"}


def _find_package_schemas_dir() -> Path | None:
    """Locate shared/schemas/ as a sibling of the forgeops/ package. Works
    in this repo's dev layout; a standalone install that doesn't ship
    shared/ will report this check as a warning, not a hard failure."""
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "shared" / "schemas" / "current_state.schema.json"
        if candidate.is_file():
            return candidate.parent
        if (ancestor / "pyproject.toml").is_file():
            break
    return None


def run_doctor(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
) -> CommandResult:
    checks: list[Check] = []

    if sys.version_info[:2] >= MIN_PYTHON:
        checks.append(Check(
            "python-version", "Python version", "pass",
            f"{platform.python_version()} >= {'.'.join(map(str, MIN_PYTHON))}",
        ))
    else:
        checks.append(Check(
            "python-version", "Python version", "fail",
            f"{platform.python_version()} is older than required {'.'.join(map(str, MIN_PYTHON))}",
        ))

    try:
        importlib.import_module("forgeops")
        checks.append(Check("forgeops-importable", "forgeops package importable", "pass", "import forgeops succeeded"))
    except ImportError as exc:
        checks.append(Check("forgeops-importable", "forgeops package importable", "fail", f"import failed: {exc}"))

    version = git_version()
    if version:
        checks.append(Check("git-available", "git executable", "pass", version))
    else:
        checks.append(Check("git-available", "git executable", "fail", "git was not found on PATH or did not respond"))

    repo_root: Path | None = None
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
        checks.append(Check("repo-discovery", "Repository discovery", "pass", str(repo_root)))
    except RepoNotFoundError as exc:
        checks.append(Check("repo-discovery", "Repository discovery", "warning", str(exc)))

    config = None
    if repo_root is not None:
        try:
            config = load_config(repo_root)
            checks.append(Check(
                "config-validity", "Configuration validity", "pass",
                "pyproject.toml [tool.forgeops] is valid (or absent, using defaults)",
            ))
        except ConfigError as exc:
            checks.append(Check("config-validity", "Configuration validity", "fail", str(exc)))

        log_dir_name = config["log_dir"] if config else "logs"
        log_dir = repo_root / log_dir_name
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            probe = log_dir / ".forgeops-doctor-write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            checks.append(Check("log-dir-writable", "Log directory writable", "pass", str(log_dir)))
        except OSError as exc:
            checks.append(Check("log-dir-writable", "Log directory writable", "fail", f"{log_dir}: {exc}"))
    else:
        checks.append(Check("config-validity", "Configuration validity", "informational", "skipped - no repository found"))
        checks.append(Check("log-dir-writable", "Log directory writable", "informational", "skipped - no repository found"))

    schemas_dir = _find_package_schemas_dir()
    if schemas_dir is not None:
        checks.append(Check(
            "schema-availability", "State schema availability", "pass",
            str(schemas_dir / "current_state.schema.json"),
        ))
    else:
        checks.append(Check(
            "schema-availability", "State schema availability", "warning",
            "shared/schemas/current_state.schema.json not found alongside this install",
        ))

    claude_path = shutil.which("claude")
    checks.append(Check(
        "claude-executable", "claude CLI (optional)",
        "pass" if claude_path else "warning", claude_path or "not found on PATH",
    ))
    codex_path = shutil.which("codex")
    checks.append(Check(
        "codex-executable", "codex CLI (optional)",
        "pass" if codex_path else "warning", codex_path or "not found on PATH",
    ))

    checks.append(Check(
        "os-shell-context", "OS / shell context", "informational",
        f"{platform.system()} {platform.release()}",
        detail={"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
    ))

    exit_code = _compute_exit_code(checks)
    result = CommandResult(
        command="doctor",
        schema_version=SCHEMA_VERSION,
        generated_at=iso_now(clock),
        repo_root=str(repo_root) if repo_root else None,
        exit_code=exit_code,
        summary=_summarize(checks, exit_code),
        checks=checks,
    )

    if write_log and repo_root is not None:
        LogWriter(repo_root, "doctor", clock=clock).write("doctor.log", _render_log_text(result))

    return result


def _compute_exit_code(checks: list[Check]) -> int:
    statuses = {c.status for c in checks}
    if any(c.status == "fail" and c.id in CONFIG_CHECK_IDS for c in checks):
        return exit_codes.INVALID_CONFIG
    if "fail" in statuses:
        return exit_codes.COMMAND_EXECUTION_FAILURE
    if "warning" in statuses:
        return exit_codes.WARNINGS_PRESENT
    return exit_codes.SUCCESS


def _summarize(checks: list[Check], exit_code: int) -> str:
    counts: dict[str, int] = {}
    for c in checks:
        counts[c.status] = counts.get(c.status, 0) + 1
    parts = ", ".join(f"{v} {k}" for k, v in counts.items())
    return f"{parts} (exit {exit_code})"


def _render_log_text(result: CommandResult) -> str:
    lines = [f"forgeops doctor - {result.generated_at}", f"repo_root: {result.repo_root}", ""]
    lines += [f"[{c.status}] {c.id}: {c.message}" for c in result.checks]
    return "\n".join(lines)


def render_human(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops doctor", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    return "\n".join(lines)
