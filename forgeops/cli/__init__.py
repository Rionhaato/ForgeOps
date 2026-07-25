"""Command-line entry points. Phase 2A implemented doctor/status/audit;
Phase 2B added changed/test --targeted; Phase 2C adds test --full and
release-check. Every other command named in the mission brief is
intentionally not registered yet and prints a clear "not implemented"
message rather than being silently absent - see docs/cli-architecture.md.

Top-level exception handling (Phase 2B, Part 5): run_fn()/render_fn()
calls are wrapped in a single boundary here. Expected user/configuration
errors (missing repo, invalid config, missing git) are already caught
*inside* each run_* function and returned as a normal CommandResult with
the correct exit code - they never reach this boundary. Only a genuinely
unexpected exception (a real bug) is caught here, converted to exit code
6 (INTERNAL_ERROR) with a redacted message and diagnostic log, unless
--debug or FORGEOPS_DEBUG is set, in which case the raw traceback is
allowed through for local debugging."""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from forgeops.cli import agent as agent_cmd
from forgeops.cli import audit as audit_cmd
from forgeops.cli import changed as changed_cmd
from forgeops.cli import checkpoint as checkpoint_cmd
from forgeops.cli import cleanup as cleanup_cmd
from forgeops.cli import doctor as doctor_cmd
from forgeops.cli import handoff as handoff_cmd
from forgeops.cli import init as init_cmd
from forgeops.cli import process_list as process_list_cmd
from forgeops.cli import release_check as release_check_cmd
from forgeops.cli import resume_context as resume_context_cmd
from forgeops.cli import status as status_cmd
from forgeops.cli import task as task_cmd
from forgeops.cli import test as test_cmd
from forgeops.cli import worktree as worktree_cmd
from forgeops.core import exit_codes
from forgeops.core.paths import find_repo_root
from forgeops.core.result import CommandResult
from forgeops.core.timestamps import path_timestamp
from forgeops.security.redact import redact_text

PHASE_2A_COMMANDS = ("doctor", "status", "audit")
NOT_YET_IMPLEMENTED_COMMANDS = (
    "agents", "approvals",
    "validate-config", "install", "uninstall",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forgeops", description="ForgeOps: deterministic project-state and safety toolkit.")
    parser.add_argument(
        "--debug", action="store_true",
        help="show a raw traceback on an unexpected internal error instead of a sanitized summary "
             "(equivalent to setting FORGEOPS_DEBUG=1)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in PHASE_2A_COMMANDS:
        sub = subparsers.add_parser(name, help=f"forgeops {name}")
        sub.add_argument("--json", action="store_true", help="emit structured JSON instead of human-readable text")
        sub.add_argument("--repo", default=None, help="path to the repository to inspect (default: discover from the current directory)")

    changed_sub = subparsers.add_parser("changed", help="forgeops changed")
    changed_sub.add_argument("--json", action="store_true")
    changed_sub.add_argument("--repo", default=None)
    changed_sub.add_argument("--staged", action="store_true", help="show only staged files")
    changed_sub.add_argument("--unstaged", action="store_true", help="show only unstaged tracked files")
    changed_sub.add_argument("--untracked", action="store_true", help="show only untracked files")

    test_sub = subparsers.add_parser("test", help="forgeops test --targeted | --full")
    test_sub.add_argument("--json", action="store_true")
    test_sub.add_argument("--repo", default=None)
    test_mode_group = test_sub.add_mutually_exclusive_group()
    test_mode_group.add_argument("--targeted", action="store_true", help="plan/run tests for the current working-tree changes")
    test_mode_group.add_argument("--full", action="store_true", help="run the complete supported test suite(s) for every detected technology")
    test_sub.add_argument("--plan", action="store_true", help="show the plan, execute nothing")
    test_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show exactly what would run, execute nothing")

    release_check_sub = subparsers.add_parser("release-check", help="forgeops release-check")
    release_check_sub.add_argument("--json", action="store_true")
    release_check_sub.add_argument("--repo", default=None)

    checkpoint_sub = subparsers.add_parser("checkpoint", help="forgeops checkpoint")
    checkpoint_sub.add_argument("--json", action="store_true")
    checkpoint_sub.add_argument("--repo", default=None)
    checkpoint_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show what would be written, write nothing")

    handoff_sub = subparsers.add_parser("handoff", help="forgeops handoff")
    handoff_sub.add_argument("--json", action="store_true")
    handoff_sub.add_argument("--repo", default=None)
    handoff_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show what would be written, write nothing")

    process_list_sub = subparsers.add_parser("process-list", help="forgeops process-list")
    process_list_sub.add_argument("--json", action="store_true")
    process_list_sub.add_argument("--repo", default=None)

    cleanup_sub = subparsers.add_parser("cleanup", help="forgeops cleanup")
    cleanup_sub.add_argument("--json", action="store_true")
    cleanup_sub.add_argument("--repo", default=None)
    cleanup_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="report cleanup candidates, take no action (default behavior)")
    cleanup_sub.add_argument("--execute", action="store_true", help="actually attempt graceful termination / stale-record removal for eligible candidates")

    resume_context_sub = subparsers.add_parser("resume-context", help="forgeops resume-context")
    resume_context_sub.add_argument("--json", action="store_true")
    resume_context_sub.add_argument("--repo", default=None)

    init_sub = subparsers.add_parser("init", help="forgeops init [PATH]")
    init_sub.add_argument("path", nargs="?", default=None, help="target directory to initialize (default: current directory)")
    init_sub.add_argument("--json", action="store_true")
    init_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show what would be created/preserved/blocked, write nothing")

    worktree_sub = subparsers.add_parser("worktree", help="forgeops worktree list | create | remove")
    worktree_subparsers = worktree_sub.add_subparsers(dest="worktree_command", required=True)

    worktree_list_sub = worktree_subparsers.add_parser("list", help="forgeops worktree list")
    worktree_list_sub.add_argument("--repo", default=None)
    worktree_list_sub.add_argument("--json", action="store_true")

    worktree_create_sub = worktree_subparsers.add_parser("create", help="forgeops worktree create NAME")
    worktree_create_sub.add_argument("name", help="worktree name (single path segment - no separators, no absolute paths)")
    worktree_create_sub.add_argument("--repo", default=None)
    worktree_create_sub.add_argument("--json", action="store_true")
    worktree_create_sub.add_argument("--branch", default=None, help="branch to create (default: forgeops/<name>)")
    worktree_create_sub.add_argument("--base", default=None, help="base ref to create the worktree from (default: HEAD)")
    worktree_create_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show what would be created, write/mutate nothing")

    worktree_remove_sub = worktree_subparsers.add_parser("remove", help="forgeops worktree remove NAME")
    worktree_remove_sub.add_argument("name", help="worktree name (must match an active ForgeOps registry entry)")
    worktree_remove_sub.add_argument("--repo", default=None)
    worktree_remove_sub.add_argument("--json", action="store_true")
    worktree_remove_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show what would be removed, mutate nothing")
    worktree_remove_sub.add_argument("--confirm", action="store_true", help="actually perform the removal (required unless --dry-run)")
    worktree_remove_sub.add_argument(
        "--delete-branch", dest="delete_branch", action="store_true",
        help="also delete the ForgeOps-owned branch via a normal, non-force `git branch -d` (requires --confirm)",
    )

    task_sub = subparsers.add_parser("task", help="forgeops task create | show | list | validate | close | assign | unassign | assign-agent | unassign-agent | request-approval | approve | reject | cancel-approval")
    task_subparsers = task_sub.add_subparsers(dest="task_command", required=True)

    task_create_sub = task_subparsers.add_parser("create", help="forgeops task create TITLE")
    task_create_sub.add_argument("title", help="short human-readable task title")
    task_create_sub.add_argument("--repo", default=None)
    task_create_sub.add_argument("--json", action="store_true")
    task_create_sub.add_argument("--spec-file", dest="spec_file", default=None, help="path to a local file whose content becomes SPEC.md verbatim")
    task_create_sub.add_argument("--acceptance", dest="acceptance_file", default=None, help="path to a local text/JSON file of acceptance criteria")
    task_create_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show what would be created, write nothing")

    task_show_sub = task_subparsers.add_parser("show", help="forgeops task show TASK_ID")
    task_show_sub.add_argument("task_id", help="task ID, e.g. task-0001")
    task_show_sub.add_argument("--repo", default=None)
    task_show_sub.add_argument("--json", action="store_true")

    task_list_sub = task_subparsers.add_parser("list", help="forgeops task list")
    task_list_sub.add_argument("--repo", default=None)
    task_list_sub.add_argument("--json", action="store_true")
    task_list_sub.add_argument("--status", dest="status_filter", default=None, help="only show tasks with this exact status")

    task_validate_sub = task_subparsers.add_parser("validate", help="forgeops task validate TASK_ID")
    task_validate_sub.add_argument("task_id", help="task ID, e.g. task-0001")
    task_validate_sub.add_argument("--repo", default=None)
    task_validate_sub.add_argument("--json", action="store_true")

    task_close_sub = task_subparsers.add_parser("close", help="forgeops task close TASK_ID")
    task_close_sub.add_argument("task_id", help="task ID, e.g. task-0001")
    task_close_sub.add_argument("--repo", default=None)
    task_close_sub.add_argument("--json", action="store_true")
    task_close_sub.add_argument("--result-file", dest="result_file", default=None, help="path to a local file whose content becomes RESULT.md verbatim")
    task_close_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show the planned state transition, mutate nothing")
    task_close_sub.add_argument("--confirm", action="store_true", help="actually perform the closure (required unless --dry-run)")

    task_assign_sub = task_subparsers.add_parser("assign", help="forgeops task assign TASK_ID WORKTREE_NAME")
    task_assign_sub.add_argument("task_id", help="task ID, e.g. task-0001")
    task_assign_sub.add_argument("worktree_name", help="name of an existing, active, unassigned ForgeOps-managed worktree")
    task_assign_sub.add_argument("--repo", default=None)
    task_assign_sub.add_argument("--json", action="store_true")
    task_assign_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show what would be assigned, mutate nothing")

    task_unassign_sub = task_subparsers.add_parser("unassign", help="forgeops task unassign TASK_ID")
    task_unassign_sub.add_argument("task_id", help="task ID, e.g. task-0001")
    task_unassign_sub.add_argument("--repo", default=None)
    task_unassign_sub.add_argument("--json", action="store_true")
    task_unassign_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show what would be unassigned, mutate nothing")
    task_unassign_sub.add_argument("--confirm", action="store_true", help="actually perform the unassignment (required unless --dry-run)")

    task_assign_agent_sub = task_subparsers.add_parser("assign-agent", help="forgeops task assign-agent TASK_ID AGENT_ID")
    task_assign_agent_sub.add_argument("task_id", help="task ID, e.g. task-0001")
    task_assign_agent_sub.add_argument("agent_id", help="ID of an existing, registered, unassigned agent")
    task_assign_agent_sub.add_argument("--repo", default=None)
    task_assign_agent_sub.add_argument("--json", action="store_true")
    task_assign_agent_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show what would be assigned, mutate nothing")

    task_unassign_agent_sub = task_subparsers.add_parser("unassign-agent", help="forgeops task unassign-agent TASK_ID")
    task_unassign_agent_sub.add_argument("task_id", help="task ID, e.g. task-0001")
    task_unassign_agent_sub.add_argument("--repo", default=None)
    task_unassign_agent_sub.add_argument("--json", action="store_true")
    task_unassign_agent_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show what would be unassigned, mutate nothing")
    task_unassign_agent_sub.add_argument("--confirm", action="store_true", help="actually perform the unassignment (required unless --dry-run)")

    task_request_approval_sub = task_subparsers.add_parser("request-approval", help="forgeops task request-approval TASK_ID --actor ACTOR")
    task_request_approval_sub.add_argument("task_id", help="task ID, e.g. task-0001")
    task_request_approval_sub.add_argument("--actor", required=True, help="explicit human actor requesting approval (e.g. 'joshua') - never inferred")
    task_request_approval_sub.add_argument("--reason", default=None, help="optional free-text reason")
    task_request_approval_sub.add_argument("--repo", default=None)
    task_request_approval_sub.add_argument("--json", action="store_true")
    task_request_approval_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show the planned transition, mutate nothing")

    task_approve_sub = task_subparsers.add_parser("approve", help="forgeops task approve TASK_ID --actor ACTOR")
    task_approve_sub.add_argument("task_id", help="task ID, e.g. task-0001")
    task_approve_sub.add_argument("--actor", required=True, help="explicit human actor approving the task - never inferred")
    task_approve_sub.add_argument("--reason", default=None, help="optional free-text reason")
    task_approve_sub.add_argument("--repo", default=None)
    task_approve_sub.add_argument("--json", action="store_true")
    task_approve_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show the planned transition, mutate nothing")
    task_approve_sub.add_argument("--confirm", action="store_true", help="actually perform the approval (required unless --dry-run)")

    task_reject_sub = task_subparsers.add_parser("reject", help="forgeops task reject TASK_ID --actor ACTOR --reason TEXT")
    task_reject_sub.add_argument("task_id", help="task ID, e.g. task-0001")
    task_reject_sub.add_argument("--actor", required=True, help="explicit human actor rejecting the task - never inferred")
    task_reject_sub.add_argument("--reason", default=None, help="reason for rejection (required to actually reject)")
    task_reject_sub.add_argument("--repo", default=None)
    task_reject_sub.add_argument("--json", action="store_true")
    task_reject_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show the planned transition, mutate nothing")
    task_reject_sub.add_argument("--confirm", action="store_true", help="actually perform the rejection (required unless --dry-run)")

    task_cancel_approval_sub = task_subparsers.add_parser("cancel-approval", help="forgeops task cancel-approval TASK_ID --actor ACTOR")
    task_cancel_approval_sub.add_argument("task_id", help="task ID, e.g. task-0001")
    task_cancel_approval_sub.add_argument("--actor", required=True, help="explicit human actor cancelling the pending request - never inferred")
    task_cancel_approval_sub.add_argument("--reason", default=None, help="optional free-text reason")
    task_cancel_approval_sub.add_argument("--repo", default=None)
    task_cancel_approval_sub.add_argument("--json", action="store_true")
    task_cancel_approval_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show the planned transition, mutate nothing")
    task_cancel_approval_sub.add_argument("--confirm", action="store_true", help="actually perform the cancellation (required unless --dry-run)")

    agent_sub = subparsers.add_parser("agent", help="forgeops agent register | list | show")
    agent_subparsers = agent_sub.add_subparsers(dest="agent_command", required=True)

    agent_register_sub = agent_subparsers.add_parser("register", help="forgeops agent register AGENT_ID --kind KIND")
    agent_register_sub.add_argument("agent_id", help="agent identifier - lowercase letters, digits, '-', '_' only")
    agent_register_sub.add_argument("--kind", required=True, help="one of: claude, codex, specialist, rocky")
    agent_register_sub.add_argument("--display-name", dest="display_name", default=None, help="human-readable name (default: the agent ID itself)")
    agent_register_sub.add_argument("--repo", default=None)
    agent_register_sub.add_argument("--json", action="store_true")
    agent_register_sub.add_argument("--dry-run", dest="dry_run", action="store_true", help="show what would be registered, write nothing")

    agent_list_sub = agent_subparsers.add_parser("list", help="forgeops agent list")
    agent_list_sub.add_argument("--repo", default=None)
    agent_list_sub.add_argument("--json", action="store_true")
    agent_list_sub.add_argument("--kind", dest="kind_filter", default=None, help="only show agents of this exact kind")

    agent_show_sub = agent_subparsers.add_parser("show", help="forgeops agent show AGENT_ID")
    agent_show_sub.add_argument("agent_id", help="agent ID")
    agent_show_sub.add_argument("--repo", default=None)
    agent_show_sub.add_argument("--json", action="store_true")

    for name in NOT_YET_IMPLEMENTED_COMMANDS:
        sub = subparsers.add_parser(name, help=f"forgeops {name} (not yet implemented)")
        sub.add_argument("--json", action="store_true")
        sub.add_argument("--repo", default=None)

    return parser


_SIMPLE_MODULES = {
    "doctor": doctor_cmd,
    "status": status_cmd,
    "audit": audit_cmd,
}


def _debug_enabled(args: argparse.Namespace) -> bool:
    if getattr(args, "debug", False):
        return True
    return os.environ.get("FORGEOPS_DEBUG", "").strip().lower() not in ("", "0", "false")


def _best_effort_repo_root(repo_arg: str | None) -> Path | None:
    try:
        start = Path(repo_arg) if repo_arg else Path.cwd()
        return find_repo_root(start)
    except OSError:
        return None


def _write_diagnostic_log(repo_root: Path | None, command: str, exc: BaseException) -> str | None:
    if repo_root is None:
        return None
    try:
        tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        redacted = redact_text(tb_text)
        log_dir = repo_root / "logs" / command / path_timestamp()
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "internal_error.log"
        path.write_text(redacted, encoding="utf-8")
        return str(path)
    except OSError:
        return None


def _run_command(args: argparse.Namespace) -> CommandResult:
    if args.command == "changed":
        return changed_cmd.run_changed(
            args.repo, staged=args.staged, unstaged=args.unstaged, untracked=args.untracked,
        )
    if args.command == "test":
        if args.full:
            return test_cmd.run_full_test(args.repo, plan_only=args.plan, dry_run=args.dry_run)
        return test_cmd.run_test_targeted(args.repo, plan_only=args.plan, dry_run=args.dry_run)
    if args.command == "release-check":
        return release_check_cmd.run_release_check(args.repo)
    if args.command == "checkpoint":
        return checkpoint_cmd.run_checkpoint(args.repo, dry_run=args.dry_run)
    if args.command == "handoff":
        return handoff_cmd.run_handoff(args.repo, dry_run=args.dry_run)
    if args.command == "process-list":
        return process_list_cmd.run_process_list(args.repo)
    if args.command == "cleanup":
        # --dry-run wins if both flags are somehow given - default to safety.
        execute = args.execute and not args.dry_run
        return cleanup_cmd.run_cleanup(args.repo, execute=execute)
    if args.command == "resume-context":
        return resume_context_cmd.run_resume_context(args.repo)
    if args.command == "init":
        return init_cmd.run_init(args.path, dry_run=args.dry_run)
    if args.command == "worktree":
        if args.worktree_command == "list":
            return worktree_cmd.run_worktree_list(args.repo)
        if args.worktree_command == "remove":
            return worktree_cmd.run_worktree_remove(
                args.name, args.repo, dry_run=args.dry_run, confirm=args.confirm, delete_branch=args.delete_branch,
            )
        return worktree_cmd.run_worktree_create(
            args.name, args.repo, dry_run=args.dry_run, branch=args.branch, base=args.base,
        )
    if args.command == "task":
        if args.task_command == "create":
            return task_cmd.run_task_create(
                args.title, args.repo, dry_run=args.dry_run,
                spec_file=args.spec_file, acceptance_file=args.acceptance_file,
            )
        if args.task_command == "show":
            return task_cmd.run_task_show(args.task_id, args.repo)
        if args.task_command == "list":
            return task_cmd.run_task_list(args.repo, status_filter=args.status_filter)
        if args.task_command == "validate":
            return task_cmd.run_task_validate(args.task_id, args.repo)
        if args.task_command == "assign":
            return task_cmd.run_task_assign(args.task_id, args.worktree_name, args.repo, dry_run=args.dry_run)
        if args.task_command == "unassign":
            return task_cmd.run_task_unassign(args.task_id, args.repo, dry_run=args.dry_run, confirm=args.confirm)
        if args.task_command == "assign-agent":
            return task_cmd.run_task_assign_agent(args.task_id, args.agent_id, args.repo, dry_run=args.dry_run)
        if args.task_command == "unassign-agent":
            return task_cmd.run_task_unassign_agent(args.task_id, args.repo, dry_run=args.dry_run, confirm=args.confirm)
        if args.task_command == "request-approval":
            return task_cmd.run_task_request_approval(
                args.task_id, args.repo, actor=args.actor, reason=args.reason, dry_run=args.dry_run,
            )
        if args.task_command == "approve":
            return task_cmd.run_task_approve(
                args.task_id, args.repo, actor=args.actor, reason=args.reason, dry_run=args.dry_run, confirm=args.confirm,
            )
        if args.task_command == "reject":
            return task_cmd.run_task_reject(
                args.task_id, args.repo, actor=args.actor, reason=args.reason, dry_run=args.dry_run, confirm=args.confirm,
            )
        if args.task_command == "cancel-approval":
            return task_cmd.run_task_cancel_approval(
                args.task_id, args.repo, actor=args.actor, reason=args.reason, dry_run=args.dry_run, confirm=args.confirm,
            )
        return task_cmd.run_task_close(
            args.task_id, args.repo, dry_run=args.dry_run, confirm=args.confirm, result_file=args.result_file,
        )
    if args.command == "agent":
        if args.agent_command == "register":
            return agent_cmd.run_agent_register(
                args.agent_id, args.kind, args.repo, dry_run=args.dry_run, display_name=args.display_name,
            )
        if args.agent_command == "list":
            return agent_cmd.run_agent_list(args.repo, kind_filter=args.kind_filter)
        return agent_cmd.run_agent_show(args.agent_id, args.repo)
    # Resolved via getattr on the module, not a pre-bound reference, so
    # that monkeypatching e.g. forgeops.cli.doctor_cmd.run_doctor (the
    # normal way tests substitute behavior) actually takes effect - a
    # dict built once at import time with direct function references
    # would silently keep using the original, unpatched function.
    module = _SIMPLE_MODULES[args.command]
    run_fn = getattr(module, f"run_{args.command}")
    return run_fn(args.repo)


def _render_result(args: argparse.Namespace, result: CommandResult) -> str:
    if args.command == "changed":
        return changed_cmd.render_human(result)
    if args.command == "test":
        return test_cmd.render_human(result)
    if args.command == "release-check":
        return release_check_cmd.render_human(result)
    if args.command == "checkpoint":
        return checkpoint_cmd.render_human(result)
    if args.command == "handoff":
        return handoff_cmd.render_human(result)
    if args.command == "process-list":
        return process_list_cmd.render_human(result)
    if args.command == "cleanup":
        return cleanup_cmd.render_human(result)
    if args.command == "resume-context":
        return resume_context_cmd.render_human(result)
    if args.command == "init":
        return init_cmd.render_human(result)
    if args.command == "worktree":
        return worktree_cmd.render_human(result)
    if args.command == "task":
        return task_cmd.render_human(result)
    if args.command == "agent":
        return agent_cmd.render_human(result)
    module = _SIMPLE_MODULES[args.command]
    return module.render_human(result)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "test" and not (args.targeted or args.full):
        print(
            "forgeops test: pass --targeted or --full (release-check composes --full; "
            "see docs/targeted-testing.md).",
            file=sys.stderr,
        )
        return 1

    dispatchable = args.command in _SIMPLE_MODULES or args.command in (
        "changed", "test", "release-check", "checkpoint", "handoff",
        "process-list", "cleanup", "resume-context", "init", "worktree", "task", "agent",
    )
    if not dispatchable:
        print(
            f"forgeops {args.command}: not yet implemented (see docs/cli-architecture.md for current scope). "
            "See .agent/HANDOFF.md for current progress.",
            file=sys.stderr,
        )
        return 1

    debug = _debug_enabled(args)

    try:
        result = _run_command(args)
        rendered = _render_result(args, result)
    except Exception as exc:  # noqa: BLE001 - deliberate top-level boundary; see module docstring
        if debug:
            raise
        repo_root = _best_effort_repo_root(getattr(args, "repo", None) or getattr(args, "path", None))
        log_path = _write_diagnostic_log(repo_root, args.command, exc)
        message = redact_text(f"forgeops {args.command}: internal error ({type(exc).__name__}: {exc})")
        if args.json:
            payload = {
                "command": args.command,
                "exit_code": exit_codes.INTERNAL_ERROR,
                "error": type(exc).__name__,
                "message": message,
                "diagnostic_log": log_path,
            }
            print(json.dumps(payload, indent=2))
        else:
            print(message, file=sys.stderr)
            print(
                f"diagnostic log: {log_path}" if log_path else "(no repository context available - diagnostic log not written)",
                file=sys.stderr,
            )
            print("re-run with --debug (or FORGEOPS_DEBUG=1) for a full traceback", file=sys.stderr)
        return exit_codes.INTERNAL_ERROR

    if args.json:
        print(result.to_json())
    else:
        print(rendered)

    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
