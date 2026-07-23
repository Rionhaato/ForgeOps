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
from forgeops.cli import test as test_cmd
from forgeops.core import exit_codes
from forgeops.core.paths import find_repo_root
from forgeops.core.result import CommandResult
from forgeops.core.timestamps import path_timestamp
from forgeops.security.redact import redact_text

PHASE_2A_COMMANDS = ("doctor", "status", "audit")
NOT_YET_IMPLEMENTED_COMMANDS = (
    "worktree", "agents", "approvals",
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
        "process-list", "cleanup", "resume-context", "init",
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
