"""Installed-safe access to bundled reference workloads."""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from clousight_bench.core.errors import UserInputError


def reference_workload_path(name: str) -> Path:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise UserInputError(f"invalid reference workload name: {name!r}")
    resource = files("clousight_bench.resources.workloads").joinpath(name)
    path = Path(str(resource))
    if not (path / "manifest.yaml").is_file():
        raise UserInputError(f"reference workload not found: {name!r}")
    return path
