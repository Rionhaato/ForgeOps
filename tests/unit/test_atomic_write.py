"""Tests for forgeops/state/atomic_write.py: the shared atomic
file-replacement primitive used by both `checkpoint` and `handoff`."""
from __future__ import annotations

import os

import pytest

from forgeops.state.atomic_write import atomic_write_text


def test_writes_new_file(tmp_path):
    target = tmp_path / "out.txt"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_creates_parent_directories(tmp_path):
    target = tmp_path / "a" / "b" / "c" / "out.txt"
    atomic_write_text(target, "nested")
    assert target.read_text(encoding="utf-8") == "nested"


def test_replaces_existing_file_content(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("old content that is longer than the new one", encoding="utf-8")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


def test_no_temporary_files_left_behind_after_success(tmp_path):
    target = tmp_path / "out.txt"
    atomic_write_text(target, "hello")
    leftovers = [p for p in tmp_path.iterdir() if p != target]
    assert leftovers == []


def test_destination_untouched_when_write_fails(tmp_path, monkeypatch):
    target = tmp_path / "out.txt"
    target.write_text("original", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(os, "fsync", boom)

    with pytest.raises(OSError, match="simulated disk failure"):
        atomic_write_text(target, "new content that must never land")

    assert target.read_text(encoding="utf-8") == "original"


def test_no_partial_temp_file_left_behind_after_failure(tmp_path, monkeypatch):
    target = tmp_path / "out.txt"

    def boom(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(os, "fsync", boom)

    with pytest.raises(OSError):
        atomic_write_text(target, "new content")

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_destination_untouched_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "out.txt"
    target.write_text("original", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_text(target, "new content")

    assert target.read_text(encoding="utf-8") == "original"
    # The failed temp file must be cleaned up, not left as debris.
    leftovers = [p for p in tmp_path.iterdir() if p != target]
    assert leftovers == []


def test_supports_path_with_spaces(tmp_path):
    spacey_dir = tmp_path / "a directory with spaces"
    target = spacey_dir / "out file.txt"
    atomic_write_text(target, "spacey content")
    assert target.read_text(encoding="utf-8") == "spacey content"
