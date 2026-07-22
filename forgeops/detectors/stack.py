"""Stack detection from manifest and marker-file evidence only - never
from directory names alone (a directory called "backend" proves nothing
by itself). Manifests/markers can live in any subdirectory (a mixed
React/FastAPI repo typically has backend/requirements.txt and
frontend/package.json, not both at the repo root) - matching is done by
basename against the relative paths tree_scan.py found, and the matched
relative path is kept as evidence rather than a bare filename."""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from forgeops.detectors.tree_scan import TreeScan


@dataclass(frozen=True)
class StackFinding:
    technology: str
    confidence: str  # "high" | "medium" | "low"
    evidence: list[str] = field(default_factory=list)
    ambiguous: bool = False


def _by_basename(relative_paths: list[str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for rel in relative_paths:
        basename = rel.rsplit("/", 1)[-1]
        index.setdefault(basename, []).append(rel)
    return index


def _read_text(repo_root: Path, relative: str) -> str | None:
    try:
        return (repo_root / relative).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _package_json_deps(repo_root: Path, package_json_paths: list[str]) -> dict[str, str]:
    deps: dict[str, str] = {}
    for rel in package_json_paths:
        text = _read_text(repo_root, rel)
        if text is None:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        for section in ("dependencies", "devDependencies"):
            section_data = data.get(section)
            if isinstance(section_data, dict):
                deps.update({k: str(v) for k, v in section_data.items()})
    return deps


def detect_stack(repo_root: Path, tree: TreeScan) -> list[StackFinding]:
    findings: list[StackFinding] = []
    manifests = _by_basename(tree.dependency_manifests)
    markers = _by_basename(tree.stack_marker_files)

    # --- Python ---
    py_manifest_evidence: list[str] = []
    for name in ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"):
        py_manifest_evidence.extend(manifests.get(name, []))
    if py_manifest_evidence:
        findings.append(StackFinding("python", "high", py_manifest_evidence))
    elif any(f.endswith(".py") for f in tree.scannable_text_files):
        findings.append(StackFinding("python", "low", ["*.py files present, no manifest found"], ambiguous=True))

    # --- pytest ---
    pytest_evidence: list[str] = list(markers.get("pytest.ini", [])) + list(markers.get("conftest.py", []))
    for rel in manifests.get("pyproject.toml", []):
        text = _read_text(repo_root, rel)
        if text is None:
            continue
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            continue
        if "pytest" in data.get("tool", {}):
            pytest_evidence.append(f"{rel} [tool.pytest.ini_options]")
    for rel in manifests.get("requirements.txt", []):
        text = (_read_text(repo_root, rel) or "").lower()
        if "pytest" in text:
            pytest_evidence.append(rel)
    if pytest_evidence:
        findings.append(StackFinding("pytest", "high", pytest_evidence))

    # --- FastAPI ---
    fastapi_evidence: list[str] = []
    for basename in ("requirements.txt", "pyproject.toml"):
        for rel in manifests.get(basename, []):
            text = (_read_text(repo_root, rel) or "").lower()
            if "fastapi" in text:
                fastapi_evidence.append(rel)
    if fastapi_evidence:
        findings.append(StackFinding("fastapi", "high", fastapi_evidence))

    # --- Node ---
    package_json_paths = manifests.get("package.json", [])
    if package_json_paths:
        findings.append(StackFinding("node", "high", package_json_paths))

    # --- Package managers ---
    lockfile_by_pm = {"npm": "package-lock.json", "pnpm": "pnpm-lock.yaml", "yarn": "yarn.lock"}
    detected_any_pm = False
    for pm_name, lockfile_name in lockfile_by_pm.items():
        lockfile_paths = manifests.get(lockfile_name, [])
        if lockfile_paths:
            detected_any_pm = True
            findings.append(StackFinding(pm_name, "high", lockfile_paths))
    if not detected_any_pm and package_json_paths:
        findings.append(
            StackFinding("package-manager", "low", ["package.json present, no lockfile found"], ambiguous=True)
        )

    # --- React / Vite / Jest / Vitest (from package.json dependency names) ---
    deps = _package_json_deps(repo_root, package_json_paths)
    dep_keys_lower = {k.lower() for k in deps}

    def _config_marker_paths(prefix: str) -> list[str]:
        found: list[str] = []
        for basename, paths in markers.items():
            if basename.startswith(prefix):
                found.extend(paths)
        return found

    if "react" in dep_keys_lower:
        findings.append(StackFinding("react", "high", [f"{p}: dependencies.react" for p in package_json_paths]))

    vite_markers = _config_marker_paths("vite.config")
    if "vite" in dep_keys_lower or vite_markers:
        evidence = [f"{p}: dependencies.vite" for p in package_json_paths if "vite" in dep_keys_lower] + vite_markers
        findings.append(StackFinding("vite", "high", evidence))

    jest_markers = _config_marker_paths("jest.config")
    if "jest" in dep_keys_lower or jest_markers:
        evidence = [f"{p}: dependencies.jest" for p in package_json_paths if "jest" in dep_keys_lower] + jest_markers
        findings.append(StackFinding("jest", "high", evidence))

    vitest_markers = _config_marker_paths("vitest.config")
    if "vitest" in dep_keys_lower or vitest_markers:
        evidence = [f"{p}: dependencies.vitest" for p in package_json_paths if "vitest" in dep_keys_lower] + vitest_markers
        findings.append(StackFinding("vitest", "high", evidence))

    # --- Mixed repository ---
    has_python = any(f.technology in ("python", "fastapi") for f in findings)
    has_node = any(f.technology in ("node", "react") for f in findings)
    if has_python and has_node:
        findings.append(
            StackFinding(
                "mixed-python-node",
                "high",
                ["python and node evidence both present in the same repository"],
            )
        )

    return findings
