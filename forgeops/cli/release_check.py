"""`forgeops release-check` - a read-only release-readiness gate. Never
pushes, deploys, publishes, configures a remote, or mutates the target
repository. Aggregates the existing doctor/audit/test --full results
rather than re-implementing their checks, adds a small set of
release-specific gates (working-tree cleanliness, branch/HEAD
availability, and a dependency-free compile/build validation), and
exposes one clear overall release-ready verdict. See
docs/release-check.md."""
from __future__ import annotations

import sys
from pathlib import Path

from forgeops.cli.audit import run_audit
from forgeops.cli.doctor import run_doctor
from forgeops.cli.test import run_full_test
from forgeops.core import exit_codes
from forgeops.core.config import ConfigError, load_config
from forgeops.core.git import get_branch_state, get_head, get_status, git_version
from forgeops.core.paths import RepoNotFoundError, resolve_repo_root
from forgeops.core.result import Check, CommandResult
from forgeops.core.subprocess_utils import run as run_subprocess
from forgeops.core.timestamps import Clock, iso_now
from forgeops.detectors.stack import detect_stack
from forgeops.detectors.tree_scan import scan_repo_tree
from forgeops.reporting.logs import LogWriter

SCHEMA_VERSION = 1
DEFAULT_CONFIG_FALLBACK = {"oversized_file_bytes": 5 * 1024 * 1024, "secret_scan_max_file_bytes": 2 * 1024 * 1024}
COMPILEALL_TIMEOUT_SECONDS = 120.0

# The set of exit codes that still count as "release ready" - a clean
# pass, or non-blocking warnings only. Everything else (a blocking
# finding, invalid config, a missing repo, a failed required command, or
# an internal error) means NOT ready. Deliberately reuses the existing
# seven codes rather than inventing an eighth - see docs/cli-exit-codes.md.
READY_EXIT_CODES = {exit_codes.SUCCESS, exit_codes.WARNINGS_PRESENT}


def run_release_check(
    repo_arg: str | None,
    cwd: Path | None = None,
    clock: Clock | None = None,
    write_log: bool = True,
) -> CommandResult:
    if git_version() is None:
        return CommandResult(
            command="release-check",
            schema_version=SCHEMA_VERSION,
            generated_at=iso_now(clock),
            repo_root=None,
            exit_code=exit_codes.COMMAND_EXECUTION_FAILURE,
            summary="NOT RELEASE READY - git executable not found or unresponsive",
            checks=[Check("git-available", "git executable", "fail", "git was not found on PATH or did not respond")],
            data={"release_ready": False},
        )

    try:
        repo_root = resolve_repo_root(repo_arg, cwd)
    except RepoNotFoundError as exc:
        return CommandResult(
            command="release-check",
            schema_version=SCHEMA_VERSION,
            generated_at=iso_now(clock),
            repo_root=None,
            exit_code=exit_codes.REPO_NOT_FOUND,
            summary=f"NOT RELEASE READY - {exc}",
            checks=[Check("repo-discovery", "Repository discovery", "fail", str(exc))],
            data={"release_ready": False},
        )

    repo_root_str = str(repo_root)
    checks: list[Check] = [Check("repo-discovery", "Repository discovery", "pass", repo_root_str)]

    try:
        config = load_config(repo_root)
    except ConfigError as exc:
        config = dict(DEFAULT_CONFIG_FALLBACK)
        checks.append(Check("config-validity", "Configuration validity", "warning", str(exc)))

    # --- Gate: working-tree cleanliness ---
    status_summary = get_status(repo_root)
    if status_summary.clean:
        checks.append(Check("working-tree-clean", "Working tree", "pass", "clean"))
    else:
        checks.append(Check(
            "working-tree-clean", "Working tree", "warning",
            f"dirty - {len(status_summary.staged)} staged, {len(status_summary.modified)} modified, "
            f"{len(status_summary.untracked)} untracked",
        ))

    # --- Gate: branch and HEAD availability ---
    branch_state = get_branch_state(repo_root)
    head = get_head(repo_root)
    if not branch_state.has_commits or head is None:
        checks.append(Check("branch-head-availability", "Branch/HEAD availability", "fail", "repository has no commits yet"))
    elif branch_state.detached:
        checks.append(Check("branch-head-availability", "Branch/HEAD availability", "warning", f"detached HEAD at {head}"))
    else:
        checks.append(Check("branch-head-availability", "Branch/HEAD availability", "pass", f"{branch_state.branch} @ {head}"))

    # --- Gate: compile/build validation (dependency-free only) ---
    tree = scan_repo_tree(repo_root, config["oversized_file_bytes"], config["secret_scan_max_file_bytes"])
    stack_findings = detect_stack(repo_root, tree)
    stack_by_tech = {f.technology: f for f in stack_findings}
    compileall_raw_output: str | None = None

    has_python = any(t in stack_by_tech for t in ("python", "fastapi", "pytest"))
    if has_python:
        py_evidence = stack_by_tech.get("python", stack_by_tech.get("fastapi", stack_by_tech.get("pytest")))
        target_dir = _group_root_dir(repo_root, py_evidence.evidence if py_evidence else [])
        proc = run_subprocess(
            [sys.executable, "-m", "compileall", "-q", str(target_dir)],
            cwd=repo_root, timeout=COMPILEALL_TIMEOUT_SECONDS,
        )
        compileall_raw_output = f"$ python -m compileall -q {target_dir}\n\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"
        if proc.error is not None or proc.timed_out:
            checks.append(Check(
                "compile-validation-python", "Compile validation (Python)", "fail",
                proc.error or f"timed out after {COMPILEALL_TIMEOUT_SECONDS}s",
            ))
        elif proc.returncode == 0:
            checks.append(Check("compile-validation-python", "Compile validation (Python)", "pass", f"python -m compileall clean for {target_dir}"))
        else:
            checks.append(Check(
                "compile-validation-python", "Compile validation (Python)", "fail",
                f"compileall reported syntax errors (exit {proc.returncode}) - see raw log",
            ))
    else:
        checks.append(Check("compile-validation-python", "Compile validation (Python)", "informational", "unavailable: no Python stack detected"))

    has_node = any(t in stack_by_tech for t in ("node", "react"))
    if has_node:
        checks.append(Check(
            "compile-validation-node", "Compile/build validation (Node)", "informational",
            "unavailable: Node build validation requires installed dependencies, which ForgeOps does not install automatically",
        ))
    else:
        checks.append(Check("compile-validation-node", "Compile/build validation (Node)", "informational", "unavailable: no Node stack detected"))

    own_exit_code = _worst_own_exit_code(checks)

    # --- Aggregate: doctor, audit, full test suite (existing logic, not reimplemented) ---
    doctor_result = run_doctor(repo_root_str, clock=clock, write_log=False)
    audit_result = run_audit(repo_root_str, clock=clock, write_log=False)
    test_result = run_full_test(repo_root_str, clock=clock, write_log=write_log)

    checks.extend(Check(f"doctor.{c.id}", f"[doctor] {c.label}", c.status, c.message, c.detail) for c in doctor_result.checks)
    checks.extend(Check(f"audit.{c.id}", f"[audit] {c.label}", c.status, c.message, c.detail) for c in audit_result.checks)
    checks.extend(Check(f"test.{c.id}", f"[test --full] {c.label}", c.status, c.message, c.detail) for c in test_result.checks)

    overall_exit_code = exit_codes.worst(own_exit_code, doctor_result.exit_code, audit_result.exit_code, test_result.exit_code)
    release_ready = overall_exit_code in READY_EXIT_CODES

    blocking = [c.id for c in checks if c.status in ("blocked", "fail")]
    warnings_ = [c.id for c in checks if c.status == "warning"]

    data = {
        "release_ready": release_ready,
        "overall_exit_code": overall_exit_code,
        "blocking_checks": blocking,
        "warning_checks": warnings_,
        "gate_results": {
            "doctor": {"exit_code": doctor_result.exit_code, "summary": doctor_result.summary},
            "audit": {"exit_code": audit_result.exit_code, "summary": audit_result.summary},
            "test_full": {"exit_code": test_result.exit_code, "summary": test_result.summary},
        },
    }

    verdict = "RELEASE READY" if release_ready else "NOT RELEASE READY"
    summary = f"{verdict} - {len(blocking)} blocker(s), {len(warnings_)} warning(s) (exit {overall_exit_code})"

    result = CommandResult(
        command="release-check",
        schema_version=SCHEMA_VERSION,
        generated_at=iso_now(clock),
        repo_root=repo_root_str,
        exit_code=overall_exit_code,
        summary=summary,
        checks=checks,
        data=data,
    )

    if write_log:
        writer = LogWriter(repo_root, "release-check", clock=clock)
        if compileall_raw_output is not None:
            writer.write("compileall.log", compileall_raw_output)
        writer.write("release-check.log", _render_log_text(result))

    return result


def _group_root_dir(repo_root: Path, evidence_paths: list[str]) -> Path:
    if not evidence_paths:
        return repo_root
    first = evidence_paths[0]
    if "/" not in first:
        return repo_root
    return repo_root / first.rsplit("/", 1)[0]


def _worst_own_exit_code(checks: list[Check]) -> int:
    statuses = {c.status for c in checks}
    if "blocked" in statuses:
        return exit_codes.BLOCKED
    if "fail" in statuses:
        return exit_codes.COMMAND_EXECUTION_FAILURE
    if "warning" in statuses:
        return exit_codes.WARNINGS_PRESENT
    return exit_codes.SUCCESS


def _render_log_text(result: CommandResult) -> str:
    lines = [f"forgeops release-check - {result.generated_at}", f"repo_root: {result.repo_root}", ""]
    lines += [f"[{c.status}] {c.id}: {c.message}" for c in result.checks]
    lines.append("")
    lines.append(result.summary)
    return "\n".join(lines)


def render_human(result: CommandResult) -> str:
    from forgeops.cli.render import render_checks
    lines = ["forgeops release-check", f"summary: {result.summary}", ""]
    lines.extend(render_checks(result.checks))
    return "\n".join(lines)
