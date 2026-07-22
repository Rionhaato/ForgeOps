"""`forgeops audit` - strictly read-only repository audit. Never deletes,
moves, edits, stages, commits, ignores, quarantines, rewrites, or
auto-fixes anything - it only reports. See docs/audit-security-model.md."""
from __future__ import annotations

from pathlib import Path

from forgeops.core import exit_codes
from forgeops.core.config import ConfigError, load_config
from forgeops.core.git import get_ahead_behind, get_branch_state, get_head, get_status, git_version
from forgeops.core.paths import RepoNotFoundError, resolve_repo_root
from forgeops.core.result import Check, CommandResult
from forgeops.core.timestamps import Clock, iso_now
from forgeops.detectors.tree_scan import scan_repo_tree
from forgeops.reporting.logs import LogWriter
from forgeops.security.dangerous_files import scan_paths as scan_dangerous_paths
from forgeops.security.secret_scan import scan_file as scan_file_for_secrets
from forgeops.state.schema import check_current_state

SCHEMA_VERSION = 1
MAX_SECRET_FINDINGS_LISTED = 200
MAX_PATHS_LISTED = 50
DEFAULT_CONFIG_FALLBACK = {"oversized_file_bytes": 5 * 1024 * 1024, "secret_scan_max_file_bytes": 2 * 1024 * 1024}


def run_audit(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
) -> CommandResult:
    if git_version() is None:
        return CommandResult(
            command="audit",
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
            command="audit",
            schema_version=SCHEMA_VERSION,
            generated_at=iso_now(clock),
            repo_root=None,
            exit_code=exit_codes.REPO_NOT_FOUND,
            summary=str(exc),
            checks=[Check("repo-discovery", "Repository discovery", "fail", str(exc))],
        )

    checks: list[Check] = []

    try:
        config = load_config(repo_root)
    except ConfigError as exc:
        config = dict(DEFAULT_CONFIG_FALLBACK)
        checks.append(Check("config-validity", "Configuration validity", "warning", str(exc)))

    # 1. Repository snapshot (informational context, not a pass/fail check).
    branch_state = get_branch_state(repo_root)
    head = get_head(repo_root)
    ahead_behind = get_ahead_behind(repo_root)
    status_summary = get_status(repo_root)
    checks.append(Check(
        "repository-snapshot", "Repository snapshot", "informational",
        f"branch={branch_state.branch!r} head={head} staged={len(status_summary.staged)} "
        f"modified={len(status_summary.modified)} untracked={len(status_summary.untracked)}",
        detail={
            "branch": branch_state.branch, "detached": branch_state.detached, "head": head,
            "ahead": ahead_behind.ahead, "behind": ahead_behind.behind,
        },
    ))

    # Single bounded filesystem walk powers most of the remaining checks.
    tree = scan_repo_tree(repo_root, config["oversized_file_bytes"], config["secret_scan_max_file_bytes"])

    # 2. Git safety findings: dangerous filename patterns among changed paths.
    candidate_paths = sorted(set(status_summary.staged) | set(status_summary.modified) | set(status_summary.untracked))
    dangerous = scan_dangerous_paths(candidate_paths)
    if dangerous:
        for match in dangerous:
            checks.append(Check(
                "git-safety", f"Dangerous file pattern: {match.path}", "blocked",
                f"matches pattern {match.pattern!r} - do not commit without confirming this is intentional",
                detail={
                    "category": "git-safety", "file": match.path, "pattern": match.pattern,
                    "severity": "high",
                    "remediation": "Unstage before committing (git restore --staged <file>) or confirm this is intentional.",
                },
            ))
    else:
        checks.append(Check("git-safety", "Dangerous file patterns in working-tree changes", "pass", "none detected"))

    # 3. Secret-pattern scan (never scans files already classified as env/db/media/browser-state).
    secret_findings = []
    secret_exemptions = []
    extra_allow_patterns = tuple(config.get("allow_secret_paths", []))
    for rel in tree.scannable_text_files:
        findings, exemptions = scan_file_for_secrets(
            repo_root, rel, config["secret_scan_max_file_bytes"], extra_allow_patterns,
        )
        secret_findings.extend(findings)
        secret_exemptions.extend(exemptions)
    if secret_findings:
        for finding in secret_findings[:MAX_SECRET_FINDINGS_LISTED]:
            checks.append(Check(
                "secret-scan", f"Possible secret in {finding.file}:{finding.line}", "blocked", finding.redacted_match,
                detail={
                    "category": finding.category, "file": finding.file, "line": finding.line,
                    "severity": finding.severity, "remediation": finding.remediation,
                },
            ))
        if len(secret_findings) > MAX_SECRET_FINDINGS_LISTED:
            checks.append(Check(
                "secret-scan-truncated", "Secret scan findings truncated", "informational",
                f"{len(secret_findings) - MAX_SECRET_FINDINGS_LISTED} additional findings not listed",
            ))
    else:
        checks.append(Check(
            "secret-scan", "Secret-pattern scan", "pass",
            f"{len(tree.scannable_text_files)} files scanned, no matches",
        ))

    # Every honored forgeops:allow-secret exemption is reported explicitly -
    # never silently absorbed into a clean "no matches" result - so an
    # audit reader can see exactly where and why scanning was skipped,
    # without the suppressed value ever appearing anywhere.
    for exemption in secret_exemptions[:MAX_SECRET_FINDINGS_LISTED]:
        checks.append(Check(
            "secret-scan-exemption", f"Exempted match in {exemption.file}:{exemption.line}", "informational",
            exemption.reason,
            detail={"category": exemption.category, "file": exemption.file, "line": exemption.line, "reason": exemption.reason},
        ))

    def _category(check_id: str, label: str, paths: list[str], status: str) -> None:
        if not paths:
            checks.append(Check(check_id, label, "pass", "none found"))
            return
        shown = paths[:MAX_PATHS_LISTED]
        truncated = len(paths) > MAX_PATHS_LISTED
        message = ", ".join(shown) + (f" (+{len(paths) - MAX_PATHS_LISTED} more)" if truncated else "")
        checks.append(Check(check_id, label, status, message, detail={"paths": shown, "count": len(paths), "truncated": truncated}))

    # 4. Likely secret-bearing / privacy-risk file categories - paths only, contents never read.
    _category("env-files-real", "Real environment files (.env* - contents never read)", tree.env_files_real, "warning")
    _category("env-files-example", "Example environment files (.env.example)", tree.env_files_example, "informational")
    _category("browser-state-files", "Browser/session state files (contents never read)", tree.browser_state_files, "warning")
    _category("db-files", "Database files", tree.db_files, "informational")
    _category("media-model-files", "Media / model artifact files", tree.media_model_files, "informational")

    # 5. Generated-artifact and cache/temp directories.
    _category("generated-artifact-dirs", "Generated-artifact directories (not descended into)", tree.generated_artifact_dirs, "informational")
    _category("cache-temp-dirs", "Cache / temp directories", tree.cache_temp_dirs, "informational")

    # 6. Oversized files.
    if tree.oversized_files:
        shown = tree.oversized_files[:MAX_PATHS_LISTED]
        message = ", ".join(f"{p} ({size // 1024}KB)" for p, size in shown)
        checks.append(Check(
            "oversized-files", "Oversized files", "warning", message,
            detail={"files": [{"path": p, "bytes": size} for p, size in shown], "count": len(tree.oversized_files)},
        ))
    else:
        checks.append(Check("oversized-files", "Oversized files", "pass", "none found"))

    # 7. Dependency manifests / instruction files.
    _category("dependency-manifests", "Dependency manifests", tree.dependency_manifests, "informational")
    _category("instruction-files", "Project instruction files", tree.instruction_files, "informational")

    # 8. Nested git repositories.
    _category("nested-git-repos", "Nested git repositories", tree.nested_git_repos, "warning")

    # 9. Suspicious untracked content: sensitive-category files not yet gitignored.
    untracked_set = set(status_summary.untracked)
    sensitive_all = set(tree.env_files_real) | set(tree.browser_state_files) | set(tree.db_files)
    suspicious_untracked = sorted(sensitive_all & untracked_set)
    _category("suspicious-untracked", "Sensitive files that are untracked (not yet gitignored)", suspicious_untracked, "warning")

    # 10. State-file validation.
    state_path = repo_root / ".agent" / "CURRENT_STATE.json"
    state_check = check_current_state(state_path)
    if not state_check.exists:
        checks.append(Check("state-file-validation", ".agent/CURRENT_STATE.json", "informational", "not present"))
    elif state_check.valid:
        checks.append(Check("state-file-validation", ".agent/CURRENT_STATE.json", "pass", "present and structurally valid"))
    else:
        detail = state_check.error or f"missing keys: {', '.join(state_check.missing_keys)}"
        checks.append(Check("state-file-validation", ".agent/CURRENT_STATE.json", "warning", detail))

    exit_code = _compute_exit_code(checks)
    result = CommandResult(
        command="audit",
        schema_version=SCHEMA_VERSION,
        generated_at=iso_now(clock),
        repo_root=str(repo_root),
        exit_code=exit_code,
        summary=_summarize(checks, exit_code),
        checks=checks,
        data={"files_scanned_for_secrets": len(tree.scannable_text_files)},
    )

    if write_log:
        LogWriter(repo_root, "audit", clock=clock).write("audit.log", _render_log_text(result))

    return result


def _compute_exit_code(checks: list[Check]) -> int:
    statuses = {c.status for c in checks}
    if "blocked" in statuses:
        return exit_codes.BLOCKED
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
    lines = [f"forgeops audit - {result.generated_at}", f"repo_root: {result.repo_root}", ""]
    lines += [f"[{c.status}] {c.id}: {c.message}" for c in result.checks]
    return "\n".join(lines)


def render_human(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops audit", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    return "\n".join(lines)
