"""Tests for the shared filesystem helpers (:mod:`forgeo.io`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from forgeo.io import atomic_write_text


def test_atomic_write_creates_file_with_content(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "file.txt"
    atomic_write_text(target, "hello\n")
    assert target.read_text(encoding="utf-8") == "hello\n"


def test_atomic_write_overwrites_existing_content(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    atomic_write_text(target, "clean\n")
    leftovers = [
        p for p in tmp_path.iterdir() if p.name.startswith(f".{target.name}.")
    ]
    assert leftovers == []


def test_atomic_write_failure_removes_temp_and_preserves_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "file.txt"
    target.write_text("keep me", encoding="utf-8")

    def boom(src, dst):
        raise OSError("rename failed")

    monkeypatch.setattr("forgeo.io.os.replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, "never written")
    assert target.read_text(encoding="utf-8") == "keep me"
    assert not list(tmp_path.glob(f".{target.name}.*.tmp"))
