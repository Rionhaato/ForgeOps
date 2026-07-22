from __future__ import annotations

from pathlib import Path

from forgeops.detectors.tree_scan import scan_repo_tree

OVERSIZED = 1024
SECRET_MAX = 1024 * 1024


def test_generated_artifact_dir_not_descended_into(tmp_path: Path):
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("module.exports = {}", encoding="utf-8")
    scan = scan_repo_tree(tmp_path, OVERSIZED, SECRET_MAX)
    assert "node_modules" in scan.generated_artifact_dirs
    assert not any("node_modules" in f for f in scan.scannable_text_files)


def test_real_env_file_classified_and_excluded_from_scanning(tmp_path: Path):
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    scan = scan_repo_tree(tmp_path, OVERSIZED, SECRET_MAX)
    assert ".env" in scan.env_files_real
    assert ".env" not in scan.scannable_text_files


def test_env_example_classified_separately(tmp_path: Path):
    (tmp_path / ".env.example").write_text("KEY=placeholder", encoding="utf-8")
    scan = scan_repo_tree(tmp_path, OVERSIZED, SECRET_MAX)
    assert ".env.example" in scan.env_files_example
    assert ".env.example" not in scan.env_files_real
    assert ".env.example" not in scan.scannable_text_files


def test_db_file_classified(tmp_path: Path):
    (tmp_path / "app.sqlite3").write_bytes(b"fake db")
    scan = scan_repo_tree(tmp_path, OVERSIZED, SECRET_MAX)
    assert "app.sqlite3" in scan.db_files
    assert "app.sqlite3" not in scan.scannable_text_files


def test_browser_state_file_classified(tmp_path: Path):
    auth_dir = tmp_path / "tools" / "auth-state"
    auth_dir.mkdir(parents=True)
    (auth_dir / "qa-user.json").write_text("{}", encoding="utf-8")
    scan = scan_repo_tree(tmp_path, OVERSIZED, SECRET_MAX)
    assert "tools/auth-state/qa-user.json" in scan.browser_state_files


def test_nested_git_repo_detected_without_descending(tmp_path: Path):
    nested = tmp_path / "vendor" / "sub"
    (nested / ".git").mkdir(parents=True)
    (nested / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    scan = scan_repo_tree(tmp_path, OVERSIZED, SECRET_MAX)
    assert "vendor/sub" in scan.nested_git_repos


def test_oversized_file_detected(tmp_path: Path):
    (tmp_path / "big.bin").write_bytes(b"x" * 2048)
    scan = scan_repo_tree(tmp_path, oversized_bytes=1024, secret_scan_max_bytes=SECRET_MAX)
    paths = [p for p, _size in scan.oversized_files]
    assert "big.bin" in paths


def test_dependency_manifest_and_instruction_file_detected(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("# rules", encoding="utf-8")
    scan = scan_repo_tree(tmp_path, OVERSIZED, SECRET_MAX)
    assert "package.json" in scan.dependency_manifests
    assert "CLAUDE.md" in scan.instruction_files


def test_ordinary_source_file_is_scannable(tmp_path: Path):
    (tmp_path / "app.py").write_text("print('hi')", encoding="utf-8")
    scan = scan_repo_tree(tmp_path, OVERSIZED, SECRET_MAX)
    assert "app.py" in scan.scannable_text_files


def test_cache_temp_dir_classified(tmp_path: Path):
    (tmp_path / ".cache" / "x").mkdir(parents=True)
    scan = scan_repo_tree(tmp_path, OVERSIZED, SECRET_MAX)
    assert ".cache" in scan.cache_temp_dirs
