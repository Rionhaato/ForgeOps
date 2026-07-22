"""`forgeops changed` - classified working-tree change report. Read-only:
issues only `git status` reads, never mutates the target repository."""
from __future__ import annotations

from pathlib import Path

from forgeops.core import exit_codes
from forgeops.core.git import git_version
from forgeops.core.paths import RepoNotFoundError, resolve_repo_root
from forgeops.core.result import Check, CommandResult
from forgeops.core.timestamps import Clock, iso_now
from forgeops.detectors.changed import ChangedFile, get_changed_files
from forgeops.reporting.logs import LogWriter

SCHEMA_VERSION = 1


def _file_dict(f: ChangedFile) -> dict:
    return {
        "path": f.path,
        "old_path": f.old_path,
        "category": f.category,
        "staged": f.staged,
        "area": f.area,
        "broad_impact": f.broad_impact,
        "technology": f.technology,
    }


def run_changed(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    staged: bool = False,
    unstaged: bool = False,
    untracked: bool = False,
) -> CommandResult:
    if git_version() is None:
        return CommandResult(
            command="changed",
            schema_version=SCHEMA_VERSION,
            generated_at=iso_now(clock),
            repo_root=None,
            exit_code=exit_codes.COMMAND_EXECUTION_FAILURE,
            summary="git executable not found or unresponsive",
            checks=[Check("git-available", "git executable", "fail", "git was not found on PATH or did not respond")],
        )

    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return CommandResult(
            command="changed",
            schema_version=SCHEMA_VERSION,
            generated_at=iso_now(clock),
            repo_root=None,
            exit_code=exit_codes.REPO_NOT_FOUND,
            summary=str(exc),
            checks=[Check("repo-discovery", "Repository discovery", "fail", str(exc))],
        )

    result_set = get_changed_files(repo_root)

    # No filter flags => show everything. Any flag(s) given => show only those categories.
    any_filter = staged or unstaged or untracked
    show_staged = staged if any_filter else True
    show_unstaged = unstaged if any_filter else True
    show_untracked = untracked if any_filter else True

    staged_files = result_set.staged if show_staged else []
    unstaged_files = result_set.unstaged if show_unstaged else []
    untracked_files = result_set.untracked if show_untracked else []
    conflicted_files = result_set.conflicted  # always reported - a conflict is never something to hide

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]
    if conflicted_files:
        checks.append(Check(
            "conflicted-files", "Conflicted files", "warning",
            ", ".join(f.path for f in conflicted_files),
        ))
    else:
        checks.append(Check("conflicted-files", "Conflicted files", "pass", "none"))

    areas_touched: dict[str, int] = {}
    for f in result_set.files:
        areas_touched[f.area] = areas_touched.get(f.area, 0) + 1

    data = {
        "staged": [_file_dict(f) for f in staged_files],
        "unstaged": [_file_dict(f) for f in unstaged_files],
        "untracked": [_file_dict(f) for f in untracked_files],
        "conflicted": [_file_dict(f) for f in conflicted_files],
        "ignored_summary": result_set.ignored_summary,
        "broad_impact_files": result_set.broad_impact_files,
        "areas_touched": areas_touched,
        "has_any_changes": result_set.has_any_changes,
    }

    exit_code = exit_codes.WARNINGS_PRESENT if conflicted_files else exit_codes.SUCCESS
    summary = (
        f"{len(staged_files)} staged, {len(unstaged_files)} unstaged, "
        f"{len(untracked_files)} untracked, {len(conflicted_files)} conflicted (exit {exit_code})"
    )

    result = CommandResult(
        command="changed",
        schema_version=SCHEMA_VERSION,
        generated_at=iso_now(clock),
        repo_root=str(repo_root),
        exit_code=exit_code,
        summary=summary,
        checks=checks,
        data=data,
    )

    if write_log:
        LogWriter(repo_root, "changed", clock=clock).write("changed.log", _render_log_text(result))

    return result


def _render_log_text(result: CommandResult) -> str:
    lines = [f"forgeops changed - {result.generated_at}", f"repo_root: {result.repo_root}", ""]
    lines += [f"[{c.status}] {c.id}: {c.message}" for c in result.checks]
    lines.append("")
    lines.append(result.summary)
    return "\n".join(lines)


def render_human(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    d = result.data
    lines = ["forgeops changed", f"summary: {result.summary}", ""]

    def _section(title: str, files: list[dict]) -> None:
        if not files:
            return
        lines.append(f"{title}:")
        for f in files:
            rename = f" (was {f['old_path']})" if f.get("old_path") else ""
            impact = " [broad-impact]" if f["broad_impact"] else ""
            tech = f" ({f['technology']})" if f["technology"] else ""
            lines.append(f"  [{f['category']}] {f['path']}{rename} - {f['area']}{tech}{impact}")
        lines.append("")

    if d:
        _section("Staged", d["staged"])
        _section("Unstaged", d["unstaged"])
        _section("Untracked", d["untracked"])
        _section("Conflicted", d["conflicted"])
        if d["ignored_summary"]:
            lines.append("Ignored (generated artifacts, summary): " + ", ".join(d["ignored_summary"][:10]))
            lines.append("")

    lines.extend(render_checks(result.checks))
    return "\n".join(lines)
