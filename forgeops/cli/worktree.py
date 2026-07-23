"""`forgeops worktree list` (read-only), `forgeops worktree create`
(mutating, `--dry-run` supported), and `forgeops worktree remove`
(mutating, confirmation-gated, `--dry-run` supported). See
docs/worktrees.md for the managed worktree root, branch-naming rule,
registry schema, conflict handling, and explicit non-goals (no prune,
no merge orchestration, no agent-assignment yet).

Follows the same shape as every other command module: `run_*(repo_arg,
cwd=None, clock=None, write_log=True, ...) -> CommandResult`, plus
`render_human(result) -> str`. Unlike most commands this module has
three `run_*` entry points (`run_worktree_list`, `run_worktree_create`,
`run_worktree_remove`) sharing one `render_human`, dispatching on
`result.command` - the same pattern `forgeops test`'s
`run_test_targeted`/`run_full_test` already established."""
from __future__ import annotations

import os
from pathlib import Path

from forgeops.core import exit_codes
from forgeops.core.git import git_version
from forgeops.core.paths import RepoNotFoundError, is_protected_reference_path, resolve_repo_root
from forgeops.core.result import Check, CommandResult
from forgeops.core.timestamps import Clock, iso_now
from forgeops.reporting.logs import LogWriter
from forgeops.state.worktree_create import apply_worktree_create, build_worktree_create_plan
from forgeops.state.worktree_registry import load_registry
from forgeops.state.worktree_remove import apply_worktree_remove, build_worktree_remove_plan
from forgeops.worktrees.git_worktree import list_worktrees
from forgeops.worktrees.naming import managed_root_for

SCHEMA_VERSION = 1


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _no_git_result(command: str, clock: Clock | None) -> CommandResult:
    return CommandResult(
        command=command, schema_version=SCHEMA_VERSION, generated_at=iso_now(clock),
        repo_root=None, exit_code=exit_codes.COMMAND_EXECUTION_FAILURE,
        summary=f"{command}: git executable not found or unresponsive (exit {exit_codes.COMMAND_EXECUTION_FAILURE})",
        checks=[Check("git-available", "git executable", "fail", "git was not found on PATH or did not respond")],
    )


def _repo_not_found_result(command: str, exc: RepoNotFoundError, clock: Clock | None) -> CommandResult:
    return CommandResult(
        command=command, schema_version=SCHEMA_VERSION, generated_at=iso_now(clock),
        repo_root=None, exit_code=exit_codes.REPO_NOT_FOUND, summary=str(exc),
        checks=[Check("repo-discovery", "Repository discovery", "fail", str(exc))],
    )


# --- worktree list -----------------------------------------------------


def run_worktree_list(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("worktree-list", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("worktree-list", exc, clock)

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]

    listing = list_worktrees(repo_root)
    for warning in listing.warnings:
        checks.append(Check("worktree-list-parse", "git worktree list output parsing", "warning", warning))

    if not listing.ok:
        checks.append(Check("git-worktree-list", "git worktree list", "fail", listing.error or "unknown error"))
        exit_code = exit_codes.COMMAND_EXECUTION_FAILURE
        summary = f"worktree list: git worktree list failed (exit {exit_code})"
        return _finish(repo_root, "worktree-list", checks, {"worktrees": []}, exit_code, summary, clock, write_log)

    checks.append(Check("git-worktree-list", "git worktree list", "pass", f"{len(listing.entries)} worktree(s) found"))

    registry = load_registry(repo_root)
    if registry.warning:
        checks.append(Check("registry-schema", "Worktree registry schema", "warning", registry.warning))

    managed_root = managed_root_for(repo_root)
    primary_path = listing.entries[0].path if listing.entries else str(repo_root)
    primary_norm = _norm(primary_path)

    registry_by_path = {}
    if not registry.warning:
        registry_by_path = {_norm(r.path): r for r in registry.records if r.status == "active"}

    live_norm_paths = {_norm(e.path) for e in listing.entries}

    worktrees_data = []
    for entry in listing.entries:
        record = registry_by_path.get(_norm(entry.path))
        in_managed_root = _norm(entry.path).startswith(_norm(managed_root) + os.sep) or _norm(entry.path) == _norm(managed_root)
        worktrees_data.append({
            "path": entry.path,
            "head": entry.head,
            "branch": entry.branch,
            "detached": entry.detached,
            "bare": entry.bare,
            "locked": entry.locked,
            "locked_reason": entry.locked_reason,
            "prunable": entry.prunable,
            "prunable_reason": entry.prunable_reason,
            "is_primary": _norm(entry.path) == primary_norm,
            "in_managed_root": in_managed_root,
            "forgeops_registered": record is not None,
            "forgeops_name": record.name if record is not None else None,
        })

    stale_entries = []
    if not registry.warning:
        for record in registry.records:
            if record.status == "active" and _norm(record.path) not in live_norm_paths:
                stale_entries.append({
                    "id": record.id, "name": record.name, "path": record.path,
                    "reason": "registered but no longer present in git worktree list",
                })
    if stale_entries:
        checks.append(Check(
            "registry-stale-entries", "Worktree registry stale entries", "warning",
            f"{len(stale_entries)} stale registry entr{'y' if len(stale_entries) == 1 else 'ies'} detected "
            "(not automatically removed)",
        ))

    exit_code = _compute_list_exit_code(checks)
    summary = f"worktree list: {len(listing.entries)} worktree(s) (exit {exit_code})"
    data = {
        "repository_root": str(repo_root),
        "primary_checkout": primary_path,
        "managed_root": str(managed_root),
        "worktrees": worktrees_data,
        "stale_registry_entries": stale_entries,
    }
    return _finish(repo_root, "worktree-list", checks, data, exit_code, summary, clock, write_log)


def _compute_list_exit_code(checks: list[Check]) -> int:
    if any(c.status == "fail" for c in checks):
        return exit_codes.COMMAND_EXECUTION_FAILURE
    if any(c.status == "warning" for c in checks):
        return exit_codes.WARNINGS_PRESENT
    return exit_codes.SUCCESS


# --- worktree create -----------------------------------------------------


def run_worktree_create(
    name_arg: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    dry_run: bool = False,
    branch: str | None = None,
    base: str | None = None,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("worktree-create", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("worktree-create", exc, clock)

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]

    if is_protected_reference_path(repo_root):
        checks.append(Check(
            "reference-repo-protection", "Read-only reference repository protection", "blocked",
            f"{repo_root} is (or is beneath) the configured read-only reference repository - "
            "forgeops worktree create will never operate on it",
        ))
        exit_code = exit_codes.BLOCKED
        summary = f"worktree create: refused - {repo_root} is the read-only reference repository (exit {exit_code})"
        data = {"dry_run": dry_run, "name": name_arg, "would_proceed": False, "action": "blocked"}
        return _finish(repo_root, "worktree-create", checks, data, exit_code, summary, clock, write_log)

    plan = build_worktree_create_plan(repo_root, name_arg, branch, base)

    if plan.conflicts:
        for c in plan.conflicts:
            checks.append(Check(f"preflight-{c.key}", f"Preflight: {c.key}", "blocked", c.message))
    else:
        checks.append(Check("preflight", "Preflight", "pass", "no conflicts detected"))

    data = {
        "dry_run": dry_run,
        "name": name_arg,
        "sanitized_name": plan.sanitized_name,
        "branch": plan.branch,
        "branch_source": plan.branch_source,
        "base_ref": plan.base_ref,
        "base_commit": plan.base_commit,
        "worktree_path": str(plan.worktree_path) if plan.worktree_path else None,
        "managed_root": str(plan.managed_root),
        "conflicts": [{"key": c.key, "message": c.message} for c in plan.conflicts],
        "would_proceed": not plan.has_conflict,
    }

    if plan.has_conflict:
        data["action"] = "blocked"
        exit_code = exit_codes.BLOCKED
        summary = f"worktree create: blocked by {len(plan.conflicts)} conflict(s) (exit {exit_code})"
        return _finish(repo_root, "worktree-create", checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        data["action"] = "would_create_worktree"
        checks.append(Check(
            "write", "Worktree creation", "informational",
            f"dry-run: would create worktree at {plan.worktree_path} on new branch "
            f"'{plan.branch}' from {plan.base_ref} ({plan.base_commit}) - no git or "
            "filesystem mutation performed",
        ))
        exit_code = exit_codes.SUCCESS
        summary = f"worktree create: dry-run, would create {plan.worktree_path} (exit {exit_code})"
        return _finish(repo_root, "worktree-create", checks, data, exit_code, summary, clock, write_log)

    outcome = apply_worktree_create(repo_root, plan, clock)
    data["git_stdout"] = outcome.stdout
    data["git_stderr"] = outcome.stderr

    if not outcome.ok:
        data["action"] = "creation_failed"
        data["partial_state"] = outcome.partial_state
        data["manual_recovery_recommendation"] = (
            f"Inspect with `git -C \"{repo_root}\" worktree list` and "
            f"`git -C \"{repo_root}\" branch --list {plan.branch}`. If a partial "
            "directory, branch, or worktree registration was left behind, remove it "
            "manually only after confirming it is not needed - forgeops does not "
            "remove or prune worktrees/branches automatically in this checkpoint."
        )
        checks.append(Check("write", "Worktree creation", "fail", f"git worktree add failed: {outcome.stderr or '(no stderr)'}"))
        exit_code = exit_codes.COMMAND_EXECUTION_FAILURE
        summary = f"worktree create: git worktree add failed (exit {exit_code})"
        return _finish(repo_root, "worktree-create", checks, data, exit_code, summary, clock, write_log)

    data["action"] = "created_worktree"
    data["registry_written"] = outcome.registry_written
    checks.append(Check("write", "Worktree creation", "pass", f"created {plan.worktree_path} on new branch '{plan.branch}'"))
    if outcome.registry_error:
        data["registry_error"] = outcome.registry_error
        checks.append(Check("registry-write", "Worktree registry write", "warning", f"worktree created but registry was not updated: {outcome.registry_error}"))
        exit_code = exit_codes.WARNINGS_PRESENT
    else:
        checks.append(Check("registry-write", "Worktree registry write", "pass", "registry updated"))
        exit_code = exit_codes.SUCCESS
    summary = f"worktree create: created {plan.worktree_path} (exit {exit_code})"
    return _finish(repo_root, "worktree-create", checks, data, exit_code, summary, clock, write_log)


# --- worktree remove -----------------------------------------------------


def run_worktree_remove(
    name_arg: str,
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
    dry_run: bool = False,
    confirm: bool = False,
    delete_branch: bool = False,
) -> CommandResult:
    if git_version() is None:
        return _no_git_result("worktree-remove", clock)
    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return _repo_not_found_result("worktree-remove", exc, clock)

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]

    if is_protected_reference_path(repo_root):
        checks.append(Check(
            "reference-repo-protection", "Read-only reference repository protection", "blocked",
            f"{repo_root} is (or is beneath) the configured read-only reference repository - "
            "forgeops worktree remove will never operate on it",
        ))
        exit_code = exit_codes.BLOCKED
        summary = f"worktree remove: refused - {repo_root} is the read-only reference repository (exit {exit_code})"
        data = {"dry_run": dry_run, "confirm": confirm, "name": name_arg, "would_proceed": False, "action": "blocked"}
        return _finish(repo_root, "worktree-remove", checks, data, exit_code, summary, clock, write_log)

    plan = build_worktree_remove_plan(repo_root, name_arg, delete_branch=delete_branch)

    if plan.conflicts:
        for c in plan.conflicts:
            checks.append(Check(f"preflight-{c.key}", f"Preflight: {c.key}", "blocked", c.message))
    else:
        checks.append(Check("preflight", "Preflight", "pass", "no conflicts detected"))

    data = {
        "dry_run": dry_run,
        "confirm": confirm,
        "name": name_arg,
        "sanitized_name": plan.sanitized_name,
        "worktree_path": str(plan.worktree_path) if plan.worktree_path else None,
        "branch": plan.branch,
        "branch_is_forgeops_owned": plan.branch_is_forgeops_owned,
        "head_commit": plan.head_commit,
        "managed_root": str(plan.managed_root),
        "delete_branch_requested": delete_branch,
        "branch_delete_eligible": plan.branch_delete.eligible if delete_branch else None,
        "branch_delete_ineligible_reason": plan.branch_delete.reason if delete_branch else None,
        "conflicts": [{"key": c.key, "message": c.message} for c in plan.conflicts],
        "would_proceed": not plan.has_conflict,
    }

    if plan.has_conflict:
        data["action"] = "blocked"
        exit_code = exit_codes.BLOCKED
        summary = f"worktree remove: blocked by {len(plan.conflicts)} conflict(s) (exit {exit_code})"
        return _finish(repo_root, "worktree-remove", checks, data, exit_code, summary, clock, write_log)

    if dry_run:
        data["action"] = "would_remove_worktree"
        msg = (
            f"dry-run: would remove worktree at {plan.worktree_path} (branch '{plan.branch}') - "
            "no git or filesystem mutation performed"
        )
        if delete_branch:
            if plan.branch_delete.eligible:
                msg += f"; would also attempt normal (non-force) deletion of branch '{plan.branch}'"
            else:
                msg += f"; branch deletion requested but would be skipped: {plan.branch_delete.reason}"
        checks.append(Check("write", "Worktree removal", "informational", msg))
        exit_code = exit_codes.SUCCESS
        summary = f"worktree remove: dry-run, would remove {plan.worktree_path} (exit {exit_code})"
        return _finish(repo_root, "worktree-remove", checks, data, exit_code, summary, clock, write_log)

    if not confirm:
        data["action"] = "confirmation_required"
        checks.append(Check(
            "confirmation-required", "Confirmation required", "blocked",
            "removal is a mutating operation - pass --confirm to execute, or --dry-run to preview with zero mutation",
        ))
        exit_code = exit_codes.BLOCKED
        summary = f"worktree remove: confirmation required for {plan.worktree_path} (exit {exit_code})"
        return _finish(repo_root, "worktree-remove", checks, data, exit_code, summary, clock, write_log)

    outcome = apply_worktree_remove(repo_root, plan)
    data["git_stdout"] = outcome.stdout
    data["git_stderr"] = outcome.stderr

    if not outcome.ok:
        data["action"] = "removal_failed"
        data["partial_state"] = outcome.partial_state
        data["manual_recovery_recommendation"] = (
            f"Inspect with `git -C \"{repo_root}\" worktree list` and check whether "
            f"{plan.worktree_path} still exists on disk. forgeops does not force-remove, "
            "prune, or reset automatically - remove any leftover state manually only after "
            "confirming it is safe to do so."
        )
        checks.append(Check(
            "write", "Worktree removal", "fail",
            f"git worktree remove did not fully complete: {outcome.stderr or outcome.partial_state}",
        ))
        exit_code = exit_codes.COMMAND_EXECUTION_FAILURE
        summary = f"worktree remove: did not fully complete for {plan.worktree_path} (exit {exit_code})"
        return _finish(repo_root, "worktree-remove", checks, data, exit_code, summary, clock, write_log)

    data["action"] = "removed_worktree"
    data["registry_written"] = outcome.registry_written
    checks.append(Check("write", "Worktree removal", "pass", f"removed {plan.worktree_path}"))

    warnings_present = False
    if outcome.registry_error:
        data["registry_error"] = outcome.registry_error
        checks.append(Check(
            "registry-write", "Worktree registry write", "warning",
            f"worktree removed but registry was not updated: {outcome.registry_error}",
        ))
        warnings_present = True
    else:
        checks.append(Check("registry-write", "Worktree registry write", "pass", "registry updated"))

    if outcome.branch_deletion is not None:
        bd = outcome.branch_deletion
        data["branch_deletion"] = {"attempted": bd.attempted, "deleted": bd.deleted, "reason": bd.reason}
        if bd.deleted:
            checks.append(Check("branch-delete", "Branch deletion", "pass", f"deleted branch '{plan.branch}'"))
        else:
            checks.append(Check("branch-delete", "Branch deletion", "warning", bd.reason or "branch not deleted"))
            warnings_present = True
    elif delete_branch:
        data["branch_deletion"] = {"attempted": False, "deleted": False, "reason": plan.branch_delete.reason}
        checks.append(Check(
            "branch-delete", "Branch deletion", "warning",
            plan.branch_delete.reason or "branch deletion was not eligible",
        ))
        warnings_present = True
    else:
        data["branch_preserved"] = True

    exit_code = exit_codes.WARNINGS_PRESENT if warnings_present else exit_codes.SUCCESS
    summary = f"worktree remove: removed {plan.worktree_path} (exit {exit_code})"
    return _finish(repo_root, "worktree-remove", checks, data, exit_code, summary, clock, write_log)


def _finish(
    repo_root: Path,
    command: str,
    checks: list[Check],
    data: dict,
    exit_code: int,
    summary: str,
    clock: Clock | None,
    write_log: bool,
) -> CommandResult:
    result = CommandResult(
        command=command, schema_version=SCHEMA_VERSION, generated_at=iso_now(clock),
        repo_root=str(repo_root), exit_code=exit_code, summary=summary, checks=checks, data=data,
    )
    if write_log:
        LogWriter(repo_root, command, clock=clock).write(f"{command}.log", _render_log_text(result))
    return result


def _render_log_text(result: CommandResult) -> str:
    lines = [f"forgeops {result.command} - {result.generated_at}", f"repo_root: {result.repo_root}", ""]
    lines += [f"[{c.status}] {c.id}: {c.message}" for c in result.checks]
    lines.append("")
    lines.append(result.summary)
    return "\n".join(lines)


def render_human(result: CommandResult) -> str:
    if result.command == "worktree-list":
        return _render_human_list(result)
    if result.command == "worktree-remove":
        return _render_human_remove(result)
    return _render_human_create(result)


def _render_human_list(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops worktree list", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    worktrees = result.data.get("worktrees", [])
    if worktrees:
        lines.append("")
        for w in worktrees:
            tags = []
            if w["is_primary"]:
                tags.append("primary")
            if w["bare"]:
                tags.append("bare")
            if w["detached"]:
                tags.append("detached")
            if w["locked"]:
                tags.append("locked" + (f": {w['locked_reason']}" if w["locked_reason"] else ""))
            if w["prunable"]:
                tags.append("prunable" + (f": {w['prunable_reason']}" if w["prunable_reason"] else ""))
            if w["in_managed_root"]:
                tags.append("managed")
            if w["forgeops_registered"]:
                tags.append(f"forgeops:{w['forgeops_name']}")
            branch_display = w["branch"] or "(detached)"
            tag_display = f" [{', '.join(tags)}]" if tags else ""
            lines.append(f"- {w['path']}: {branch_display} @ {w['head'] or '?'}{tag_display}")
    return "\n".join(lines)


def _render_human_create(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops worktree create", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    data = result.data
    lines.append("")
    lines.append(f"name: {data.get('name')}")
    lines.append(f"branch: {data.get('branch')} ({data.get('branch_source')})")
    lines.append(f"base: {data.get('base_ref')} -> {data.get('base_commit')}")
    lines.append(f"worktree path: {data.get('worktree_path')}")
    lines.append(f"would proceed: {data.get('would_proceed')}")
    if data.get("conflicts"):
        lines.append("conflicts:")
        for c in data["conflicts"]:
            lines.append(f"  - {c['key']}: {c['message']}")
    if data.get("partial_state") is not None:
        lines.append(f"partial state after failure: {data['partial_state']}")
        lines.append(f"recovery: {data.get('manual_recovery_recommendation')}")
    return "\n".join(lines)


def _render_human_remove(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops worktree remove", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    data = result.data
    lines.append("")
    lines.append(f"name: {data.get('name')}")
    lines.append(f"worktree path: {data.get('worktree_path')}")
    lines.append(f"branch: {data.get('branch')}")
    lines.append(f"would proceed: {data.get('would_proceed')}")
    if data.get("conflicts"):
        lines.append("conflicts:")
        for c in data["conflicts"]:
            lines.append(f"  - {c['key']}: {c['message']}")
    if data.get("branch_deletion") is not None:
        bd = data["branch_deletion"]
        reason = f" ({bd['reason']})" if bd.get("reason") else ""
        lines.append(f"branch deletion: attempted={bd['attempted']} deleted={bd['deleted']}{reason}")
    elif data.get("branch_preserved"):
        lines.append("branch deletion: not requested - branch preserved")
    if data.get("partial_state") is not None:
        lines.append(f"partial state after failure: {data['partial_state']}")
        lines.append(f"recovery: {data.get('manual_recovery_recommendation')}")
    return "\n".join(lines)
