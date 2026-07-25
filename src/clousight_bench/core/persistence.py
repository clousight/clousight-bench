"""Write a result so a reader never sees half of one.

A benchmark record is worthless if a crash can leave it truncated, and it is
worse than worthless if a full disk silently discards it. ``atomic_write_text``
gives readers all-or-nothing visibility; ``emergency_write_text`` is the last
resort when the results directory itself cannot be written.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

EMERGENCY_DIR_NAME = "clousight-bench-emergency"


def atomic_write_text(path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` so readers see either the old or the new file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path.resolve()


def emergency_write_text(name: str, text: str) -> Path:
    """Write ``text`` into the system temp directory and return its absolute path."""
    directory = Path(tempfile.gettempdir()).resolve() / EMERGENCY_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path.resolve()
