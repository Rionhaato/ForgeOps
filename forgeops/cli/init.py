"""`forgeops init [PATH]` - safe, deterministic bootstrap of the minimum
ForgeOps governance structure for a target project. See
docs/project-init.md for the ownership/conflict model, dry-run
semantics, non-Git behavior, and explicit non-goals.

Unlike every other ForgeOps command, target resolution here never walks
upward looking for a `.git` entry (see forgeops.core.paths.normalize_path):
the target is exactly the given PATH argument, or the current working
directory if omitted - so `init` never silently initializes a different
parent/child repository than the one explicitly requested. A missing
`.git` is reported honestly, not treated as an error - git is optional
for this command alone."""
from __future__ import annotations

from pathlib import Path

from forgeops.core import exit_codes
from forgeops.core.paths import is_protected_reference_path, normalize_path
from forgeops.core.result import Check, CommandResult
from forgeops.core.timestamps import Clock, iso_now
from forgeops.reporting.logs import LogWriter
from forgeops.state.project_init import apply_init_plan, build_init_plan

SCHEMA_VERSION = 1


def run_init(
    path_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    dry_run: bool = False,
) -> CommandResult:
    start = path_arg if path_arg is not None else (cwd if cwd is not None else Path.cwd())
    target = normalize_path(start)

    if is_protected_reference_path(target):
        return CommandResult(
            command="init", schema_version=SCHEMA_VERSION, generated_at=iso_now(clock),
            repo_root=str(target), exit_code=exit_codes.BLOCKED,
            summary=f"init: refused - {target} is the read-only reference repository (exit {exit_codes.BLOCKED})",
            checks=[Check(
                "reference-repo-protection", "Read-only reference repository protection", "blocked",
                f"{target} is (or is beneath) the configured read-only reference repository - "
                "forgeops init will never initialize it",
            )],
        )

    if not target.exists():
        return CommandResult(
            command="init", schema_version=SCHEMA_VERSION, generated_at=iso_now(clock),
            repo_root=None, exit_code=exit_codes.REPO_NOT_FOUND,
            summary=f"init: path does not exist: {target} (exit {exit_codes.REPO_NOT_FOUND})",
            checks=[Check("target-resolution", "Target path resolution", "fail", f"path does not exist: {target}")],
        )
    if not target.is_dir():
        return CommandResult(
            command="init", schema_version=SCHEMA_VERSION, generated_at=iso_now(clock),
            repo_root=None, exit_code=exit_codes.REPO_NOT_FOUND,
            summary=f"init: path is not a directory: {target} (exit {exit_codes.REPO_NOT_FOUND})",
            checks=[Check("target-resolution", "Target path resolution", "fail", f"path is not a directory: {target}")],
        )

    checks: list[Check] = [Check("target-resolution", "Target path resolution", "pass", str(target))]

    plan = build_init_plan(target)
    checks.append(Check(
        "git-state", "Git repository state", "informational",
        f"git repository: yes, branch={plan.branch}" if plan.is_git_repo
        else "git repository: no (git is optional for forgeops init)",
    ))
    for p in plan.paths:
        status = "blocked" if p.state == "conflict" else "pass"
        checks.append(Check(f"preflight-{p.key}", f"Preflight: {p.relative_path}", status, f"{p.state}: {p.detail}"))

    data: dict = {
        "dry_run": dry_run,
        "target": str(target),
        "is_git_repo": plan.is_git_repo,
        "paths": [
            {"key": p.key, "path": str(p.relative_path), "kind": p.kind, "state": p.state, "detail": p.detail}
            for p in plan.paths
        ],
    }

    if plan.has_conflict:
        checks.append(Check(
            "write", "Initialization write", "informational",
            f"blocked by {len(plan.conflicts())} conflict(s) - no files were written"
            if not dry_run else
            f"dry-run: {len(plan.conflicts())} conflict(s) detected - initialization would not proceed",
        ))
        data["outcomes"] = []
        exit_code = exit_codes.BLOCKED
        summary = f"init: blocked by {len(plan.conflicts())} conflict(s) at {target} (exit {exit_code})"
        return _finish(target, checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        to_create = plan.to_be_created()
        checks.append(Check(
            "write", "Initialization write", "informational",
            f"dry-run: would create {len(to_create)} path(s), preserve {len(plan.preserved())} "
            "existing compatible path(s) - no file was written",
        ))
        data["outcomes"] = [
            {
                "key": p.key, "path": str(p.relative_path),
                "action": "would_create" if p.state == "missing" else "would_preserve",
            }
            for p in plan.paths
        ]
        exit_code = exit_codes.SUCCESS
        summary = f"init: dry-run, would initialize {target} (exit {exit_code})"
        return _finish(target, checks, data, exit_code, summary, clock, write_log)

    outcomes, error = apply_init_plan(target, plan, clock)
    data["outcomes"] = [
        {"key": o.key, "path": str(o.relative_path), "action": o.action, "ok": o.ok, "detail": o.detail}
        for o in outcomes
    ]
    if error is not None:
        checks.append(Check("write", "Initialization write", "fail", f"write failed, rolled back this run's new paths: {error}"))
        exit_code = exit_codes.COMMAND_EXECUTION_FAILURE
        summary = f"init: write failure at {target}, rolled back (exit {exit_code})"
    else:
        created = sum(1 for o in outcomes if o.action in ("created_dir", "created_file"))
        checks.append(Check(
            "write", "Initialization write", "pass",
            f"created {created} path(s), preserved {len(plan.preserved())} existing compatible path(s)",
        ))
        exit_code = exit_codes.SUCCESS
        summary = f"init: initialized {target} (exit {exit_code})"

    return _finish(target, checks, data, exit_code, summary, clock, write_log)


def _finish(
    target: Path,
    checks: list[Check],
    data: dict,
    exit_code: int,
    summary: str,
    clock: Clock | None,
    write_log: bool,
) -> CommandResult:
    result = CommandResult(
        command="init", schema_version=SCHEMA_VERSION, generated_at=iso_now(clock),
        repo_root=str(target), exit_code=exit_code, summary=summary, checks=checks, data=data,
    )
    if write_log:
        LogWriter(target, "init", clock=clock).write("init.log", _render_log_text(result))
    return result


def _render_log_text(result: CommandResult) -> str:
    lines = [f"forgeops init - {result.generated_at}", f"repo_root: {result.repo_root}", ""]
    lines += [f"[{c.status}] {c.id}: {c.message}" for c in result.checks]
    lines.append("")
    lines.append(result.summary)
    return "\n".join(lines)


def render_human(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops init", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    return "\n".join(lines)
