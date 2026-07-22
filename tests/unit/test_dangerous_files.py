from __future__ import annotations

from forgeops.security.dangerous_files import match_dangerous, scan_paths


def test_matches_real_env_file():
    match = match_dangerous("backend/.env")
    assert match is not None
    assert match.pattern == "*.env"


def test_matches_dotted_env_variant():
    match = match_dangerous("backend/.env.production")
    assert match is not None


def test_does_not_match_env_example():
    assert match_dangerous("backend/.env.example") is None


def test_matches_mp4():
    match = match_dangerous("artifacts/demo.mp4")
    assert match is not None
    assert match.pattern == "*.mp4"


def test_matches_credentials_named_json():
    match = match_dangerous("config/credentials.json")
    assert match is not None


def test_source_file_implementing_secret_scanning_is_not_flagged():
    # forgeops/security/secret_scan.py itself must not trip its own filename heuristic.
    assert match_dangerous("forgeops/security/secret_scan.py") is None
    assert match_dangerous("app/credentials_helper.js") is None


def test_data_file_named_secret_is_still_flagged():
    assert match_dangerous("config/my-secret.json") is not None
    assert match_dangerous("config/my-secret.txt") is not None


def test_ordinary_file_not_matched():
    assert match_dangerous("src/app.py") is None


def test_windows_style_path_separators_handled():
    match = match_dangerous("backend\\.env")
    assert match is not None


def test_scan_paths_returns_all_matches():
    matches = scan_paths(["a/.env", "b/normal.py", "c/.env.example", "d/secret.pem".replace(".pem", ".pem")])
    matched_paths = {m.path for m in matches}
    assert "a/.env" in matched_paths
    assert "b/normal.py" not in matched_paths
    assert "c/.env.example" not in matched_paths
