"""A half-written result file must never exist, and a failed write must land somewhere."""

import os
from pathlib import Path

import pytest

from clousight_bench.core.persistence import (
    EMERGENCY_DIR_NAME,
    atomic_write_text,
    emergency_write_text,
)


def test_atomic_write_creates_parents_and_writes_the_content(tmp_path):
    target = tmp_path / "a" / "b" / "record.json"
    written = atomic_write_text(target, '{"x":1}\n')
    assert written == target.resolve()
    assert target.read_text(encoding="utf-8") == '{"x":1}\n'


def test_atomic_write_replaces_an_existing_file(tmp_path):
    target = tmp_path / "record.json"
    atomic_write_text(target, "old")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


def test_atomic_write_leaves_no_temp_file_behind(tmp_path):
    target = tmp_path / "record.json"
    atomic_write_text(target, "content")
    assert [p.name for p in tmp_path.iterdir()] == ["record.json"]


def test_atomic_write_cleans_up_and_reraises_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "record.json"

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_text(target, "content")
    assert list(tmp_path.iterdir()) == []


def test_emergency_write_returns_an_absolute_path_under_the_temp_dir(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    path = emergency_write_text("agent-runtime-T1.3-run-1.json", '{"x":1}')
    assert path.is_absolute()
    assert path.parent.name == EMERGENCY_DIR_NAME
    assert path.parent.parent == Path(tmp_path).resolve()
    assert path.read_text(encoding="utf-8") == '{"x":1}'
