"""`forgeops status` - concise, structured repository state. Read-only:
issues only `git` read commands, never mutates the target repository."""
from __future__ import annotations

from pathlib import Path

from forgeops.core import exit_codes
from forgeops.core.config import ConfigError, load_config
from forgeops.core.git import (
    get_ahead_behind,
    get_branch_state,
    get_head,
    get_ignored_summary,
    get_last_commit,
    get_operation_state,
    get_remotes,
    get_status,
    git_version,
)
from forgeops.core.paths import RepoNotFoundError, resolve_repo_root
from forgeops.core.result import Check, CommandResult
from forgeops.core.timestamps import Clock, iso_now
from forgeops.detectors.stack import detect_stack
from forgeops.detectors.tree_scan import scan_repo_tree
from forgeops.reporting.logs import LogWriter
from forgeops.state.schema import check_current_state

SCHEMA_VERSION = 1
LIST_CAP = 50


def _capped(items: list[str]) -> tuple[list[str], bool]:
    return items[:LIST_CAP], len(items) > LIST_CAP


def run_status(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
) -> CommandResult:
    if git_version() is None:
        return CommandResult(
            command="status",
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
            command="status",
            schema_version=SCHEMA_VERSION,
            generated_at=iso_now(clock),
            repo_root=None,
            exit_code=exit_codes.REPO_NOT_FOUND,
            summary=str(exc),
            checks=[Check("repo-discovery", "Repository discovery", "fail", str(exc))],
        )

    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", str(repo_root))]

    try:
        config = load_config(repo_root)
        checks.append(Check("config-validity", "Configuration validity", "pass", "valid"))
    except ConfigError as exc:
        config = {"oversized_file_bytes": 5 * 1024 * 1024, "secret_scan_max_file_bytes": 2 * 1024 * 1024}
        checks.append(Check("config-validity", "Configuration validity", "warning", str(exc)))

    branch_state = get_branch_state(repo_root)
    head = get_head(repo_root)
    remotes = get_remotes(repo_root)
    ahead_behind = get_ahead_behind(repo_root)
    status_summary = get_status(repo_root)
    ignored = get_ignored_summary(repo_root)
    operation_state = get_operation_state(repo_root)
    last_commit = get_last_commit(repo_root)

    tree = scan_repo_tree(
        repo_root,
        oversized_bytes=config["oversized_file_bytes"],
        secret_scan_max_bytes=config["secret_scan_max_file_bytes"],
    )
    stack_findings = detect_stack(repo_root, tree)
    package_managers = [f for f in stack_findings if f.technology in ("npm", "pnpm", "yarn")]

    if branch_state.detached:
        checks.append(Check("detached-head", "HEAD state", "warning", f"detached at {head}"))
    else:
        checks.append(Check("detached-head", "HEAD state", "pass", f"on branch {branch_state.branch}"))

    if operation_state != "none":
        checks.append(Check("operation-state", "In-progress git operation", "warning", operation_state))
    else:
        checks.append(Check("operation-state", "In-progress git operation", "pass", "none"))

    checks.append(Check(
        "working-tree", "Working tree", "informational",
        "clean" if status_summary.clean else "dirty",
    ))

    state_path = repo_root / ".agent" / "CURRENT_STATE.json"
    state_check = check_current_state(state_path)
    if not state_check.exists:
        checks.append(Check("agent-state-file", ".agent/CURRENT_STATE.json", "informational", "not present"))
    elif state_check.valid:
        checks.append(Check("agent-state-file", ".agent/CURRENT_STATE.json", "pass", "present and structurally valid"))
    else:
        detail = state_check.error or f"missing keys: {', '.join(state_check.missing_keys)}"
        checks.append(Check("agent-state-file", ".agent/CURRENT_STATE.json", "warning", detail))

    handoff_exists = (repo_root / ".agent" / "HANDOFF.md").is_file()
    checks.append(Check(
        "agent-handoff-file", ".agent/HANDOFF.md", "pass" if handoff_exists else "informational",
        "present" if handoff_exists else "not present",
    ))

    if tree.instruction_files:
        checks.append(Check("instruction-files", "Project instruction files", "pass", ", ".join(tree.instruction_files)))
    else:
        checks.append(Check("instruction-files", "Project instruction files", "informational", "none found (CLAUDE.md / AGENTS.md)"))

    staged, staged_truncated = _capped(status_summary.staged)
    modified, modified_truncated = _capped(status_summary.modified)
    untracked, untracked_truncated = _capped(status_summary.untracked)

    data = {
        "repository_name": repo_root.name,
        "branch": branch_state.branch,
        "detached_head": branch_state.detached,
        "has_commits": branch_state.has_commits,
        "head": head,
        "remotes": [{"name": r.name, "url": r.url} for r in remotes],
        "upstream": ahead_behind.upstream,
        "ahead": ahead_behind.ahead,
        "behind": ahead_behind.behind,
        "staged": {"count": len(status_summary.staged), "paths": staged, "truncated": staged_truncated},
        "modified": {"count": len(status_summary.modified), "paths": modified, "truncated": modified_truncated},
        "untracked": {"count": len(status_summary.untracked), "paths": untracked, "truncated": untracked_truncated},
        "ignored_summary": ignored,
        "operation_state": operation_state,
        "clean": status_summary.clean,
        "last_commit": (
            {"sha": last_commit.sha, "author": last_commit.author, "date": last_commit.date, "subject": last_commit.subject}
            if last_commit else None
        ),
        "detected_stack": [
            {"technology": f.technology, "confidence": f.confidence, "evidence": f.evidence, "ambiguous": f.ambiguous}
            for f in stack_findings
        ],
        "package_managers": [f.technology for f in package_managers],
        "instruction_files": tree.instruction_files,
        "agent_state": {
            "current_state_json": {
                "exists": state_check.exists,
                "valid": state_check.valid,
                "missing_keys": state_check.missing_keys,
                "error": state_check.error,
            },
            "handoff_md_exists": handoff_exists,
        },
    }

    exit_code = _compute_exit_code(checks)
    result = CommandResult(
        command="status",
        schema_version=SCHEMA_VERSION,
        generated_at=iso_now(clock),
        repo_root=str(repo_root),
        exit_code=exit_code,
        summary=_summarize(branch_state, status_summary, exit_code),
        checks=checks,
        data=data,
    )

    if write_log:
        LogWriter(repo_root, "status", clock=clock).write("status.log", _render_log_text(result))

    return result


def _compute_exit_code(checks: list[Check]) -> int:
    statuses = {c.status for c in checks}
    if "fail" in statuses:
        return exit_codes.COMMAND_EXECUTION_FAILURE
    if "warning" in statuses:
        return exit_codes.WARNINGS_PRESENT
    return exit_codes.SUCCESS


def _summarize(branch_state, status_summary, exit_code: int) -> str:
    branch_label = branch_state.branch or ("detached HEAD" if branch_state.detached else "no commits yet")
    dirty_label = "clean" if status_summary.clean else (
        f"{len(status_summary.staged)} staged, {len(status_summary.modified)} modified, "
        f"{len(status_summary.untracked)} untracked"
    )
    return f"{branch_label} - {dirty_label} (exit {exit_code})"


def _render_log_text(result: CommandResult) -> str:
    lines = [f"forgeops status - {result.generated_at}", f"repo_root: {result.repo_root}", ""]
    lines += [f"[{c.status}] {c.id}: {c.message}" for c in result.checks]
    return "\n".join(lines)


def render_human(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    d = result.data
    lines = ["forgeops status", f"summary: {result.summary}", ""]
    if d:
        lines.append(f"repo: {d['repository_name']}  ({result.repo_root})")
        lines.append(f"branch: {d['branch'] or ('detached @ ' + (d['head'] or '?')[:12] if d['detached_head'] else '(no commits yet)')}")
        lines.append(f"head: {d['head']}")
        if d["remotes"]:
            lines.append("remotes: " + ", ".join(f"{r['name']} -> {r['url']}" for r in d["remotes"]))
        if d["upstream"]:
            lines.append(f"upstream: {d['upstream']} (ahead {d['ahead']}, behind {d['behind']})")
        if d["last_commit"]:
            lc = d["last_commit"]
            lines.append(f"last commit: {lc['sha'][:12]} {lc['subject']!r} ({lc['author']}, {lc['date']})")
        if d["detected_stack"]:
            lines.append("stack: " + ", ".join(f"{f['technology']}({f['confidence']})" for f in d["detected_stack"]))
        lines.append("")
    lines.extend(render_checks(result.checks))
    return "\n".join(lines)
