"""Enables `python -m forgeops` as an equivalent to the `forgeops` console
script entry point."""
import sys

from forgeops.cli import main

if __name__ == "__main__":
    sys.exit(main())
