"""Command-line entry points. Phase 2A implements doctor/status/audit;
every other command named in the mission brief is intentionally not
registered yet and prints a clear "not implemented" message rather than
being silently absent - see docs/cli-architecture.md."""
from __future__ import annotations

import argparse
import sys

from forgeops.cli import audit as audit_cmd
from forgeops.cli import doctor as doctor_cmd
from forgeops.cli import status as status_cmd
from forgeops.core.result import CommandResult

PHASE_2A_COMMANDS = ("doctor", "status", "audit")
NOT_YET_IMPLEMENTED_COMMANDS = (
    "init", "checkpoint", "handoff", "changed", "test", "release-check",
    "process-list", "cleanup", "worktree", "agents", "approvals",
    "validate-config", "install", "uninstall",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forgeops", description="ForgeOps: deterministic project-state and safety toolkit.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in PHASE_2A_COMMANDS:
        sub = subparsers.add_parser(name, help=f"forgeops {name}")
        sub.add_argument("--json", action="store_true", help="emit structured JSON instead of human-readable text")
        sub.add_argument("--repo", default=None, help="path to the repository to inspect (default: discover from the current directory)")

    for name in NOT_YET_IMPLEMENTED_COMMANDS:
        sub = subparsers.add_parser(name, help=f"forgeops {name} (not yet implemented)")
        sub.add_argument("--json", action="store_true")
        sub.add_argument("--repo", default=None)

    return parser


_DISPATCH = {
    "doctor": (doctor_cmd.run_doctor, doctor_cmd.render_human),
    "status": (status_cmd.run_status, status_cmd.render_human),
    "audit": (audit_cmd.run_audit, audit_cmd.render_human),
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command not in _DISPATCH:
        print(
            f"forgeops {args.command}: not yet implemented (see docs/cli-architecture.md for the Phase 2A scope). "
            "See .agent/HANDOFF.md for current progress.",
            file=sys.stderr,
        )
        return 1

    run_fn, render_fn = _DISPATCH[args.command]
    result: CommandResult = run_fn(args.repo)

    if args.json:
        print(result.to_json())
    else:
        print(render_fn(result))

    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
