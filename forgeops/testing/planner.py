"""Deterministic targeted-test planning. Selects test commands from cheap
evidence (filenames, directory relationships, manifests, test-naming
conventions, config files) - never a full import/dependency graph. See
docs/targeted-testing.md for the selection rules this implements.

Core policy, in order:
1. No changed files -> a successful, empty plan (nothing to test).
2. Any broad-impact changed file for a technology group -> run that
   group's full suite, never a narrowed subset.
3. A changed test file -> run that test file directly.
4. A changed source file with a same-stem test file discoverable by
   filename convention -> run that test file.
5. A changed source file with no discoverable test pairing -> broaden to
   the group's full suite (never silently select nothing).
6. A shared/cross-cutting or schema/contract change in a mixed repo ->
   broaden every technology group present, not just the one that changed.
7. No supported test command evidence for a group with changed files in
   it -> a warning naming the missing evidence, never an invented command.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from forgeops.detectors.changed import ChangedFile, ChangedFilesResult
from forgeops.detectors.stack import StackFinding, detect_stack
from forgeops.detectors.tree_scan import TreeScan, scan_repo_tree

TEST_NAME_PY_RE_PREFIX = "test_"
TEST_NAME_PY_RE_SUFFIX = "_test.py"
JS_TEST_SUFFIXES = (".test.js", ".test.ts", ".test.jsx", ".test.tsx", ".spec.js", ".spec.ts", ".spec.jsx", ".spec.tsx")


@dataclass(frozen=True)
class TestCommand:
    __test__ = False  # not a pytest test class despite the name

    command: list[str]
    cwd: str  # relative to repo root, "." for the repo root itself
    reason: str
    scope: str  # "targeted" | "broad"
    confidence: str  # "high" | "medium" | "low"
    fallback: bool
    technology: str
    log_name: str


@dataclass
class TestPlan:
    __test__ = False  # not a pytest test class despite the name

    changed_files: list[str] = field(default_factory=list)
    commands: list[TestCommand] = field(default_factory=list)
    skipped_technologies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scope: str = "none"  # "none" | "targeted" | "broad" | "mixed"

    @property
    def confidence(self) -> str:
        if not self.commands:
            return "high" if not self.changed_files else "low"
        confidences = {c.confidence for c in self.commands}
        if "low" in confidences:
            return "low"
        if "medium" in confidences:
            return "medium"
        return "high"

    @property
    def fallback(self) -> bool:
        return any(c.fallback for c in self.commands)

    @property
    def expected_log_names(self) -> list[str]:
        return [c.log_name for c in self.commands]

    def to_dict(self) -> dict:
        return {
            "changed_files": self.changed_files,
            "commands": [
                {
                    "command": c.command,
                    "cwd": c.cwd,
                    "reason": c.reason,
                    "scope": c.scope,
                    "confidence": c.confidence,
                    "fallback": c.fallback,
                    "technology": c.technology,
                    "log_name": c.log_name,
                }
                for c in self.commands
            ],
            "scope": self.scope,
            "confidence": self.confidence,
            "fallback": self.fallback,
            "skipped_technologies": self.skipped_technologies,
            "warnings": self.warnings,
            "expected_log_names": self.expected_log_names,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _group_root(evidence_paths: list[str]) -> str:
    """Best-effort directory containing a technology's manifest evidence -
    "backend" for "backend/requirements.txt", "." for a root-level manifest."""
    if not evidence_paths:
        return "."
    first = evidence_paths[0]
    if "/" not in first:
        return "."
    return first.rsplit("/", 1)[0]


def _relativize(path: str, cwd: str) -> str:
    """Rewrite a repo-root-relative path to be relative to `cwd` instead,
    so a targeted command can actually cd into a subdirectory project
    (backend/, frontend/) and pass the tool a path it understands, rather
    than always invoking from the repo root. Paths outside `cwd` (an
    unusual cross-directory match) are left as repo-root-relative,
    since there's no clean relative form without a `..` that would be
    more confusing than helpful."""
    if cwd == ".":
        return path
    prefix = cwd + "/"
    return path[len(prefix):] if path.startswith(prefix) else path


def _is_python_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith(TEST_NAME_PY_RE_PREFIX) or name.endswith(TEST_NAME_PY_RE_SUFFIX)


def _is_js_test_file(path: str) -> bool:
    return path.endswith(JS_TEST_SUFFIXES)


def _py_stem(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if name.endswith(".py"):
        name = name[:-3]
    return name


def _find_nearby_python_tests(stem: str, all_python_files: list[str]) -> list[str]:
    candidates = [f"test_{stem}.py", f"{stem}_test.py"]
    matches = [f for f in all_python_files if f.rsplit("/", 1)[-1] in candidates]
    if matches:
        return sorted(matches)
    # Looser fallback: any test file whose name contains the stem.
    loose = [f for f in all_python_files if _is_python_test_file(f) and stem in f.rsplit("/", 1)[-1]]
    return sorted(loose)


def _js_stem(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    for suffix in (".jsx", ".tsx", ".ts", ".js"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _find_nearby_js_tests(stem: str, all_js_files: list[str]) -> list[str]:
    matches = []
    for f in all_js_files:
        if not _is_js_test_file(f):
            continue
        base = f.rsplit("/", 1)[-1]
        if base.startswith(f"{stem}."):
            matches.append(f)
    return sorted(matches)


def _has_broad_impact(files: list[ChangedFile]) -> ChangedFile | None:
    for f in files:
        if f.broad_impact:
            return f
    return None


def _plan_python(
    repo_root: Path,
    group_files: list[ChangedFile],
    evidence_paths: list[str],
    all_python_files: list[str],
    pytest_evidenced: bool,
) -> tuple[TestCommand | None, str | None]:
    """Returns (command_or_None, warning_or_None)."""
    if not pytest_evidenced:
        return None, (
            "Python source changed but no pytest evidence was found (no pytest.ini, conftest.py, "
            "[tool.pytest.ini_options] in pyproject.toml, or 'pytest' in requirements.txt) - "
            "not inventing a test command."
        )

    cwd = _group_root(evidence_paths)
    broad_trigger = _has_broad_impact(group_files)
    if broad_trigger is not None:
        return TestCommand(
            command=[sys.executable, "-m", "pytest"],
            cwd=cwd,
            reason=f"broad-impact file changed: {broad_trigger.path}",
            scope="broad",
            confidence="high",
            fallback=False,
            technology="pytest",
            log_name="python-pytest.log",
        ), None

    # Pair every changed .py file independently (a test file pairs with
    # itself; a source file pairs with any nearby test found by filename
    # convention) and union the results - short-circuiting on the first
    # match found would silently drop coverage for every other changed
    # file that didn't happen to be a test file itself.
    changed_paths = [f.path for f in group_files if f.path.endswith(".py")]
    targets: set[str] = set()
    unmatched: list[str] = []
    any_direct_test_file = False
    for p in changed_paths:
        if _is_python_test_file(p):
            targets.add(p)
            any_direct_test_file = True
            continue
        nearby = _find_nearby_python_tests(_py_stem(p), all_python_files)
        if nearby:
            targets.update(nearby)
        else:
            unmatched.append(p)

    if unmatched:
        # Rule 7: any changed file without a discoverable pairing means
        # ambiguous impact for this technology - broaden safely rather
        # than run only the subset that did pair.
        return TestCommand(
            command=[sys.executable, "-m", "pytest"],
            cwd=cwd,
            reason=(
                "no direct test pairing found by filename for: "
                f"{', '.join(sorted(unmatched)[:5])}"
                f"{' (+more)' if len(unmatched) > 5 else ''}; broadening to the full suite for this technology"
            ),
            scope="broad",
            confidence="medium",
            fallback=True,
            technology="pytest",
            log_name="python-pytest.log",
        ), None

    if targets:
        relative_targets = sorted(_relativize(t, cwd) for t in targets)
        return TestCommand(
            command=[sys.executable, "-m", "pytest", *relative_targets],
            cwd=cwd,
            reason="directly changed test file(s)" if all(t in changed_paths for t in targets) else "test file(s) paired with changed source by filename convention",
            scope="targeted",
            confidence="high" if any_direct_test_file and targets <= set(changed_paths) else "medium",
            fallback=False,
            technology="pytest",
            log_name="python-pytest.log",
        ), None

    # changed_paths was empty (group_files had no .py entries, e.g. only
    # reached this function via a non-.py cross-cutting trigger) - broaden
    # for safety rather than select nothing.
    return TestCommand(
        command=[sys.executable, "-m", "pytest"],
        cwd=cwd,
        reason="no .py file evidence to pair against; broadening to the full suite for this technology",
        scope="broad",
        confidence="low",
        fallback=True,
        technology="pytest",
        log_name="python-pytest.log",
    ), None


def _node_runner_command(package_json_path: Path, lockfile_technology: str | None) -> list[str] | None:
    try:
        data = json.loads(package_json_path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return None
    scripts = data.get("scripts")
    if isinstance(scripts, dict) and "test" in scripts:
        runner = {"npm": "npm", "pnpm": "pnpm", "yarn": "yarn"}.get(lockfile_technology, "npm")
        return [runner, "test"] if runner != "npm" else ["npm", "test"]
    dev_deps = data.get("devDependencies", {})
    if isinstance(dev_deps, dict):
        if "vitest" in dev_deps:
            return ["npx", "vitest", "run"]
        if "jest" in dev_deps:
            return ["npx", "jest"]
    return None


def _plan_node(
    repo_root: Path,
    group_files: list[ChangedFile],
    evidence_paths: list[str],
    all_js_files: list[str],
    package_managers: list[str],
) -> tuple[TestCommand | None, str | None]:
    cwd = _group_root(evidence_paths)
    package_json = repo_root / cwd / "package.json" if cwd != "." else repo_root / "package.json"
    lockfile_tech = package_managers[0] if package_managers else None
    base_command = _node_runner_command(package_json, lockfile_tech)
    if base_command is None:
        return None, (
            "JavaScript/TypeScript source changed but no runnable test command was found "
            "(no package.json 'scripts.test', and no jest/vitest devDependency) - not inventing a test command."
        )

    broad_trigger = _has_broad_impact(group_files)
    if broad_trigger is not None:
        return TestCommand(
            command=base_command,
            cwd=cwd,
            reason=f"broad-impact file changed: {broad_trigger.path}",
            scope="broad",
            confidence="high",
            fallback=False,
            technology="node",
            log_name="node-test.log",
        ), None

    changed_paths = [f.path for f in group_files if any(f.path.endswith(s) for s in (".js", ".jsx", ".ts", ".tsx"))]
    targets: set[str] = set()
    unmatched: list[str] = []
    any_direct_test_file = False
    for p in changed_paths:
        if _is_js_test_file(p):
            targets.add(p)
            any_direct_test_file = True
            continue
        nearby = _find_nearby_js_tests(_js_stem(p), all_js_files)
        if nearby:
            targets.update(nearby)
        else:
            unmatched.append(p)

    if unmatched:
        return TestCommand(
            command=base_command,
            cwd=cwd,
            reason=(
                "no direct test pairing found by filename for: "
                f"{', '.join(sorted(unmatched)[:5])}"
                f"{' (+more)' if len(unmatched) > 5 else ''}; broadening to the full suite for this technology"
            ),
            scope="broad",
            confidence="medium",
            fallback=True,
            technology="node",
            log_name="node-test.log",
        ), None

    if targets:
        relative_targets = sorted(_relativize(t, cwd) for t in targets)
        return TestCommand(
            command=[*base_command, *relative_targets],
            cwd=cwd,
            reason="directly changed test file(s)" if all(t in changed_paths for t in targets) else "test file(s) paired with changed source by filename convention",
            scope="targeted",
            confidence="high" if any_direct_test_file and targets <= set(changed_paths) else "medium",
            fallback=False,
            technology="node",
            log_name="node-test.log",
        ), None

    return TestCommand(
        command=base_command,
        cwd=cwd,
        reason="no JS/TS file evidence to pair against; broadening to the full suite for this technology",
        scope="broad",
        confidence="low",
        fallback=True,
        technology="node",
        log_name="node-test.log",
    ), None


def build_full_test_plan(repo_root: Path, config: dict) -> TestPlan:
    """Full-suite counterpart to build_test_plan(): ignores changed files
    entirely (changed_files is always [] on the returned plan) and
    selects one broad command per detected, test-evidenced technology
    group, regardless of what has or hasn't changed. Reuses the same
    TestPlan/TestCommand model and the same _group_root/_node_runner_command
    helpers as targeted planning, so forgeops test --full and --targeted
    share one execution/rendering path (forgeops/testing/executor.py,
    forgeops/cli/test.py:render_human).

    Policy, mirroring build_test_plan's rules 2 and 7 exactly:
    - A detected stack with test-runner evidence (pytest for Python; a
      package.json 'scripts.test' or a jest/vitest devDependency for
      Node) -> one broad command for that stack's full suite.
    - A detected stack with no test-runner evidence -> a warning naming
      the missing evidence, never an invented command.
    - No supported stack detected at all -> a warning; empty plan.
    """
    plan = TestPlan(changed_files=[])

    tree: TreeScan = scan_repo_tree(repo_root, config["oversized_file_bytes"], config["secret_scan_max_file_bytes"])
    stack_findings: list[StackFinding] = detect_stack(repo_root, tree)
    stack_by_tech = {f.technology: f for f in stack_findings}

    has_python = any(t in stack_by_tech for t in ("python", "fastapi", "pytest"))
    has_node = any(t in stack_by_tech for t in ("node", "react"))

    if has_python:
        py_evidence = stack_by_tech.get("python", stack_by_tech.get("fastapi", stack_by_tech.get("pytest")))
        evidence_paths = py_evidence.evidence if py_evidence else []
        cwd = _group_root(evidence_paths)
        if "pytest" in stack_by_tech:
            plan.commands.append(TestCommand(
                command=[sys.executable, "-m", "pytest"],
                cwd=cwd,
                reason="full suite requested (forgeops test --full)",
                scope="broad",
                confidence="high",
                fallback=False,
                technology="pytest",
                log_name="python-pytest.log",
            ))
        else:
            plan.warnings.append(
                "Python stack detected but no pytest evidence was found (no pytest.ini, conftest.py, "
                "[tool.pytest.ini_options] in pyproject.toml, or 'pytest' in requirements.txt) - "
                "not inventing a test command."
            )
            plan.skipped_technologies.append("python")

    if has_node:
        node_evidence = stack_by_tech.get("node")
        evidence_paths = node_evidence.evidence if node_evidence else []
        package_managers = [t for t in ("npm", "pnpm", "yarn") if t in stack_by_tech]
        cwd = _group_root(evidence_paths)
        package_json = repo_root / cwd / "package.json" if cwd != "." else repo_root / "package.json"
        base_command = _node_runner_command(package_json, package_managers[0] if package_managers else None)
        if base_command is not None:
            plan.commands.append(TestCommand(
                command=base_command,
                cwd=cwd,
                reason="full suite requested (forgeops test --full)",
                scope="broad",
                confidence="high",
                fallback=False,
                technology="node",
                log_name="node-test.log",
            ))
        else:
            plan.warnings.append(
                "Node stack detected but no runnable test command was found (no package.json "
                "'scripts.test', and no jest/vitest devDependency) - not inventing a test command."
            )
            plan.skipped_technologies.append("node")

    if not has_python and not has_node:
        plan.warnings.append(
            "No supported stack (Python or Node) was detected in this repository - no test command was selected."
        )

    plan.scope = "broad" if plan.commands else "none"
    return plan


def build_test_plan(repo_root: Path, changed: ChangedFilesResult, config: dict) -> TestPlan:
    changed_paths = changed.all_paths
    plan = TestPlan(changed_files=changed_paths)

    if not changed_paths:
        plan.scope = "none"
        return plan

    tree: TreeScan = scan_repo_tree(repo_root, config["oversized_file_bytes"], config["secret_scan_max_file_bytes"])
    stack_findings: list[StackFinding] = detect_stack(repo_root, tree)
    stack_by_tech = {f.technology: f for f in stack_findings}

    all_python_files = [f for f in tree.scannable_text_files if f.endswith(".py")]
    all_js_files = [f for f in tree.scannable_text_files if f.endswith((".js", ".jsx", ".ts", ".tsx"))]

    files_by_path = {}
    for f in changed.files:
        files_by_path.setdefault(f.path, []).append(f)
    all_changed_files: list[ChangedFile] = [items[0] for items in files_by_path.values()]

    has_python = any(t in stack_by_tech for t in ("python", "fastapi", "pytest"))
    has_node = any(t in stack_by_tech for t in ("node", "react"))
    is_mixed = "mixed-python-node" in stack_by_tech

    python_changed = [f for f in all_changed_files if f.path.endswith(".py")]
    js_changed = [f for f in all_changed_files if f.path.endswith((".js", ".jsx", ".ts", ".tsx"))]

    # Rule 6: a shared/cross-cutting or schema/contract change in a mixed
    # repo broadens BOTH groups, even for the side whose files didn't change.
    cross_cutting_trigger = next(
        (f for f in all_changed_files if f.area in ("shared-cross-cutting", "database") and is_mixed),
        None,
    )

    if has_python:
        py_evidence = stack_by_tech.get("python", stack_by_tech.get("fastapi", stack_by_tech.get("pytest")))
        evidence_paths = py_evidence.evidence if py_evidence else []
        pytest_evidenced = "pytest" in stack_by_tech
        if python_changed:
            command, warning = _plan_python(repo_root, python_changed, evidence_paths, all_python_files, pytest_evidenced)
            if command is not None:
                plan.commands.append(command)
            if warning is not None:
                plan.warnings.append(warning)
        elif cross_cutting_trigger is not None and pytest_evidenced:
            plan.commands.append(TestCommand(
                command=[sys.executable, "-m", "pytest"],
                cwd=_group_root(evidence_paths),
                reason=f"shared/cross-cutting change broadens both sides: {cross_cutting_trigger.path}",
                scope="broad", confidence="high", fallback=False,
                technology="pytest", log_name="python-pytest.log",
            ))
        else:
            plan.skipped_technologies.append("python")
    elif python_changed:
        plan.warnings.append(
            "Python file(s) changed but no Python stack evidence (pyproject.toml/requirements.txt/setup.py/setup.cfg) was found."
        )

    if has_node:
        node_evidence = stack_by_tech.get("node")
        evidence_paths = node_evidence.evidence if node_evidence else []
        package_managers = [t for t in ("npm", "pnpm", "yarn") if t in stack_by_tech]
        if js_changed:
            command, warning = _plan_node(repo_root, js_changed, evidence_paths, all_js_files, package_managers)
            if command is not None:
                plan.commands.append(command)
            if warning is not None:
                plan.warnings.append(warning)
        elif cross_cutting_trigger is not None:
            cwd = _group_root(evidence_paths)
            package_json = repo_root / cwd / "package.json" if cwd != "." else repo_root / "package.json"
            base_command = _node_runner_command(package_json, package_managers[0] if package_managers else None)
            if base_command is not None:
                plan.commands.append(TestCommand(
                    command=base_command, cwd=cwd,
                    reason=f"shared/cross-cutting change broadens both sides: {cross_cutting_trigger.path}",
                    scope="broad", confidence="high", fallback=False,
                    technology="node", log_name="node-test.log",
                ))
            else:
                plan.warnings.append(
                    "Shared/cross-cutting change should broaden the Node side too, but no runnable test "
                    "command was found (no package.json 'scripts.test', and no jest/vitest devDependency)."
                )
        else:
            plan.skipped_technologies.append("node")
    elif js_changed:
        plan.warnings.append("JavaScript/TypeScript file(s) changed but no Node stack evidence (package.json) was found.")

    if not has_python and not has_node:
        plan.warnings.append(
            "No supported stack (Python or Node) was detected in this repository - no test command was selected."
        )

    if plan.commands:
        scopes = {c.scope for c in plan.commands}
        plan.scope = "broad" if scopes == {"broad"} else ("targeted" if scopes == {"targeted"} else "mixed")
    else:
        plan.scope = "none"

    return plan
