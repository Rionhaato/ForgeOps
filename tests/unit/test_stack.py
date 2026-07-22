from __future__ import annotations

import json
from pathlib import Path

from forgeops.detectors.stack import detect_stack
from forgeops.detectors.tree_scan import scan_repo_tree

OVERSIZED = 5 * 1024 * 1024
SECRET_MAX = 2 * 1024 * 1024


def _scan(repo_root: Path):
    return scan_repo_tree(repo_root, OVERSIZED, SECRET_MAX)


def test_python_detected_from_pyproject(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    findings = detect_stack(tmp_path, _scan(tmp_path))
    techs = {f.technology: f for f in findings}
    assert "python" in techs
    assert techs["python"].confidence == "high"


def test_python_low_confidence_without_manifest(tmp_path: Path):
    (tmp_path / "app.py").write_text("print(1)", encoding="utf-8")
    findings = detect_stack(tmp_path, _scan(tmp_path))
    techs = {f.technology: f for f in findings}
    assert techs["python"].confidence == "low"
    assert techs["python"].ambiguous is True


def test_directory_name_alone_does_not_imply_stack(tmp_path: Path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "notes.txt").write_text("just notes", encoding="utf-8")
    findings = detect_stack(tmp_path, _scan(tmp_path))
    assert findings == []


def test_fastapi_detected_from_requirements(tmp_path: Path):
    (tmp_path / "requirements.txt").write_text("fastapi==0.110.0\nuvicorn\n", encoding="utf-8")
    findings = detect_stack(tmp_path, _scan(tmp_path))
    techs = {f.technology for f in findings}
    assert "fastapi" in techs


def test_pytest_detected_from_ini(tmp_path: Path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    findings = detect_stack(tmp_path, _scan(tmp_path))
    techs = {f.technology for f in findings}
    assert "pytest" in techs


def test_node_react_vite_detected_from_package_json(tmp_path: Path):
    package = {"dependencies": {"react": "^18.0.0"}, "devDependencies": {"vite": "^5.0.0"}}
    (tmp_path / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    findings = detect_stack(tmp_path, _scan(tmp_path))
    techs = {f.technology for f in findings}
    assert {"node", "react", "vite", "npm"} <= techs


def test_jest_and_vitest_detected(tmp_path: Path):
    package = {"devDependencies": {"jest": "^29.0.0", "vitest": "^1.0.0"}}
    (tmp_path / "package.json").write_text(json.dumps(package), encoding="utf-8")
    findings = detect_stack(tmp_path, _scan(tmp_path))
    techs = {f.technology for f in findings}
    assert "jest" in techs
    assert "vitest" in techs


def test_package_manager_ambiguous_without_lockfile(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    findings = detect_stack(tmp_path, _scan(tmp_path))
    techs = {f.technology: f for f in findings}
    assert techs["package-manager"].ambiguous is True


def test_pnpm_and_yarn_detected_from_lockfiles(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 6", encoding="utf-8")
    findings = detect_stack(tmp_path, _scan(tmp_path))
    techs = {f.technology for f in findings}
    assert "pnpm" in techs


def test_mixed_python_node_repository_flagged(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"react": "^18.0.0"}}), encoding="utf-8")
    findings = detect_stack(tmp_path, _scan(tmp_path))
    techs = {f.technology for f in findings}
    assert "mixed-python-node" in techs


def test_manifests_in_subdirectories_are_detected(tmp_path: Path):
    """Regression test: a mixed React/FastAPI repo typically has
    backend/requirements.txt and frontend/package.json, not root-level
    manifests. Phase 2A disposable-repo validation caught detect_stack
    comparing against bare filenames while tree_scan reports full
    relative paths, silently missing every non-root-level manifest."""
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "requirements.txt").write_text("fastapi==0.110.0\n", encoding="utf-8")

    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^18.0.0"}, "devDependencies": {"vite": "^5.0.0"}}),
        encoding="utf-8",
    )

    findings = detect_stack(tmp_path, _scan(tmp_path))
    techs = {f.technology: f for f in findings}

    assert techs["python"].confidence == "high"
    assert "backend/requirements.txt" in techs["python"].evidence
    assert "fastapi" in techs
    assert "backend/requirements.txt" in techs["fastapi"].evidence
    assert "node" in techs
    assert "frontend/package.json" in techs["node"].evidence
    assert "react" in techs
    assert "vite" in techs
    assert "mixed-python-node" in techs
