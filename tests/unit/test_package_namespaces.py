"""Deterministic structural checks that `forgeops`'s package layout
matches where its logic actually lives.

Two one-line packages (`forgeops/agents/`, `forgeops/approvals/`) were
created by the Phase 1 scaffolding commit (c00b66e) and advertised
responsibilities - "Task ownership and file-lock coordination for
concurrent agents", "Approval queue and gating for security-sensitive
actions" - that were subsequently implemented somewhere else entirely,
under `forgeops/state/` and `forgeops/cli/`. They were never imported,
never documented as a public import path, and never referenced by any
tracked file. This module locks in their removal and, more importantly,
locks in the canonical locations that replaced them, so a future
checkpoint cannot silently reintroduce a second home for agent or
approval logic.

The distinction this module encodes: a package is legitimate when it
holds code (or is a *forward-looking* placeholder for a feature nobody
has built yet, like `forgeops/hooks/` and `forgeops/integrations/`); it
is a hazard when it names a responsibility that is already implemented
under a different path."""
from __future__ import annotations

import importlib
import pkgutil

import pytest

import forgeops

# Packages removed because their stated responsibility is implemented
# elsewhere - mapped to the module that actually owns it today.
RETIRED_NAMESPACES = {
    "forgeops.agents": "forgeops.state.agent_registry",
    "forgeops.approvals": "forgeops.state.task_approval",
}

# Canonical owners: module path -> symbols that must remain importable
# from it. These are the real public surfaces the CLI depends on.
CANONICAL_OWNERS = {
    "forgeops.state.agent_registry": (
        "AgentRecord", "AgentRegistryDocument",
        "load_agent_registry", "save_agent_registry", "validate_agent_id",
    ),
    "forgeops.state.agent_register": ("build_agent_register_plan", "apply_agent_register"),
    "forgeops.state.task_ownership": (
        "build_task_assign_plan", "apply_task_assign",
        "build_task_assign_agent_plan", "apply_task_assign_agent",
    ),
    "forgeops.state.task_approval": (
        "build_task_request_approval_plan", "apply_task_request_approval",
        "build_task_approve_plan", "apply_task_approve",
        "build_task_reject_plan", "apply_task_reject",
        "build_task_cancel_approval_plan", "apply_task_cancel_approval",
    ),
    "forgeops.state.task_registry": ("APPROVAL_TRANSITIONS", "ApprovalEvent", "TaskRecord", "validate_actor"),
    "forgeops.cli.agent": ("run_agent_register", "run_agent_list", "run_agent_show"),
}

# Intentional forward-looking placeholders: these name features that are
# genuinely unbuilt, so an empty package is honest rather than
# misleading. Deliberately NOT removed by this checkpoint.
INTENTIONAL_PLACEHOLDERS = ("forgeops.hooks", "forgeops.integrations")


# --- the retired namespaces are genuinely gone -------------------------------


@pytest.mark.parametrize("retired", sorted(RETIRED_NAMESPACES))
def test_retired_namespace_is_not_importable(retired):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(retired)


@pytest.mark.parametrize("retired", sorted(RETIRED_NAMESPACES))
def test_retired_namespace_is_not_discoverable_as_a_subpackage(retired):
    """Guards against a stray directory reappearing on disk without an
    explicit import - `pkgutil` walks the real package path, so this
    fails even if nothing imports it."""
    leaf = retired.rsplit(".", 1)[1]
    found = {m.name for m in pkgutil.iter_modules(forgeops.__path__)}
    assert leaf not in found, f"{retired} reappeared as a discoverable subpackage of forgeops"


# --- what replaced them is importable and complete ---------------------------


@pytest.mark.parametrize("module_path,symbols", sorted(CANONICAL_OWNERS.items()))
def test_canonical_owner_module_exposes_its_public_symbols(module_path, symbols):
    module = importlib.import_module(module_path)
    missing = [s for s in symbols if not hasattr(module, s)]
    assert not missing, f"{module_path} is missing expected symbol(s): {missing}"


@pytest.mark.parametrize("retired,replacement", sorted(RETIRED_NAMESPACES.items()))
def test_every_retired_namespace_has_a_live_replacement(retired, replacement):
    """A namespace may only be retired if the responsibility it named is
    demonstrably owned by a module that still imports."""
    assert importlib.import_module(replacement) is not None


# --- intentional placeholders are preserved ----------------------------------


@pytest.mark.parametrize("placeholder", INTENTIONAL_PLACEHOLDERS)
def test_forward_looking_placeholders_are_left_intact(placeholder):
    """`forgeops.hooks` / `forgeops.integrations` name genuinely unbuilt
    features, so they are honest placeholders - this checkpoint must not
    remove them along with the misleading ones."""
    assert importlib.import_module(placeholder) is not None


# --- package still imports and stays dependency-free -------------------------


def test_forgeops_package_still_imports_cleanly():
    """`forgeops doctor` asserts exactly this (`import forgeops`), so a
    broken package tree would surface as a doctor failure in the field."""
    assert importlib.import_module("forgeops") is not None


def test_forgeops_declares_no_required_runtime_dependencies():
    """Removing packages must not have been paid for with a dependency.
    Parsed from pyproject.toml with the stdlib only."""
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["dependencies"] == []


# --- CLI registration is unchanged -------------------------------------------


EXPECTED_TOP_LEVEL_COMMANDS = {
    "doctor", "status", "audit", "changed", "test", "release-check",
    "checkpoint", "handoff", "process-list", "cleanup", "resume-context",
    "init", "worktree", "task", "agent", "agents", "approvals",
    "validate-config", "install", "uninstall",
}

EXPECTED_TASK_SUBCOMMANDS = {
    "create", "show", "list", "validate", "close", "assign", "unassign",
    "assign-agent", "unassign-agent", "run", "request-approval", "approve",
    "reject", "cancel-approval",
}

EXPECTED_AGENT_SUBCOMMANDS = {"register", "list", "show"}


def _subparser_map(parser, dest):
    """Return the {name: subparser} mapping argparse builds for a
    subparsers action, so a caller can both assert on the names and
    descend into a specific subparser."""
    for action in parser._actions:
        if getattr(action, "dest", None) == dest and getattr(action, "choices", None):
            return dict(action.choices)
    raise AssertionError(f"no subparser action found for dest={dest!r}")


def test_top_level_command_registration_is_unchanged():
    from forgeops.cli import build_parser
    assert set(_subparser_map(build_parser(), "command")) == EXPECTED_TOP_LEVEL_COMMANDS


def test_task_and_agent_subcommand_registration_is_unchanged():
    """The `agents`/`approvals` *CLI command names* are unrelated to the
    removed Python packages and must survive untouched - they are
    pre-existing not-yet-implemented placeholder commands."""
    from forgeops.cli import build_parser
    commands = _subparser_map(build_parser(), "command")
    assert set(_subparser_map(commands["task"], "task_command")) == EXPECTED_TASK_SUBCOMMANDS
    assert set(_subparser_map(commands["agent"], "agent_command")) == EXPECTED_AGENT_SUBCOMMANDS
