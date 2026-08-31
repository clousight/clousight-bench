"""Shared CLI helpers: config/target/param parsing, exit codes, thresholds."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from clousight_bench.core.errors import (
    UserInputError,
)

_EXIT_BY_STATUS = {"completed": 0, "unsupported": 0, "failed": 1, "invalid": 1}


def _load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    config_path = Path(path)
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UserInputError(f"config not found: {config_path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        # Anything else the filesystem/decoder can raise while reading a user-
        # supplied path (a directory, unreadable permissions, non-UTF-8 bytes,
        # ...) is a bad input, not an internal bug -- report it the same way.
        raise UserInputError(f"cannot read config {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise UserInputError(f"invalid YAML in {config_path}: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise UserInputError(f"config root must be a mapping: {config_path}")
    return data


def _check_target(target: dict[str, Any]) -> dict[str, Any]:
    """Reject the legacy ``oss_bucket`` target key with an actionable hint.

    ``oss_bucket`` was a cross-cloud smell (AWS users typed it to name an S3
    bucket); it is now ``blob_bucket``. Clean break, no compat alias -- fail
    loud so a stale config surfaces immediately instead of silently naming an
    empty bucket. Returns the same target unchanged when it is clean.
    """
    if "oss_bucket" in target:
        raise UserInputError(
            "target key 'oss_bucket' was renamed to 'blob_bucket'; update your target config"
        )
    return target


def _parse_params(pairs: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise UserInputError(f"--param expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        try:
            params[key] = json.loads(value)
        except json.JSONDecodeError:
            params[key] = value
    return params


def _exit_code(record: Any) -> int:
    """Exit on the benchmark's verdict -- unless the record never reached disk.

    A run that measured perfectly but could not be written where the caller
    asked for it is not a success from a script's point of view: the file it
    is about to read is not there.
    """
    code = _EXIT_BY_STATUS[record.status]
    if record.run.stages.get("PERSIST") != "ok":
        code = max(code, 1)
    return code


def _resolve_task_id(args: argparse.Namespace) -> str:
    """The run's task_id from --benchmark (the standard way) or --task (native /
    legacy). ``--benchmark <id>`` is sugar for the canonical ``suite:<id>``."""
    benchmark = getattr(args, "benchmark", None)
    task = getattr(args, "task", None)
    if benchmark and task:
        raise UserInputError("pass either --benchmark or --task, not both")
    if benchmark:
        return benchmark if benchmark.startswith("suite:") else f"suite:{benchmark}"
    if task:
        return task
    raise UserInputError("a run needs --benchmark <id> (a registered benchmark suite) or --task <id>")


def _load_thresholds(path: str) -> dict[str, Any]:
    """Load a threshold map (YAML/JSON) for ``csbench run --assert``.

    Shape: ``{measurement_key: {min: x} | {max: y} | scalar(==min)}``.
    """
    raw = _load_config(path) if path else {}
    thresholds = raw.get("thresholds", raw) if isinstance(raw, dict) else {}
    if not isinstance(thresholds, dict) or not thresholds:
        raise UserInputError(f"--assert file {path!r} has no thresholds mapping")
    return thresholds


def _load_aggregates(results_dir: Path) -> list[dict]:
    import json

    from clousight_bench.core.runplan import AGGREGATES_DIRNAME

    agg_dir = results_dir / AGGREGATES_DIRNAME
    if not agg_dir.exists():
        return []
    best: dict[tuple[str, str, str], dict] = {}
    for path in sorted(agg_dir.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("kind") != "run_plan_aggregate":
            continue
        identity = data.get("identity", {})
        key = (identity.get("domain", ""), identity.get("task_id", ""), identity.get("adapter", ""))
        existing = best.get(key)
        this_n = data.get("plan", {}).get("repeat", 0)
        ex_n = existing.get("plan", {}).get("repeat", 0) if existing else -1
        if (
            existing is None
            or this_n > ex_n
            or (this_n == ex_n and data.get("plan_id", "") > existing.get("plan_id", ""))
        ):
            best[key] = data
    return list(best.values())
