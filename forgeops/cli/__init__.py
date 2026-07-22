"""Command-line entry points (forgeops doctor/init/audit/status/... )."""
import sys


def main() -> int:
    print(
        "forgeops: CLI not yet implemented (Phase 2 of the ForgeOps build). "
        "See .agent/HANDOFF.md for current progress.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
