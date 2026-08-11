"""The request side of a benchmark run, plus the public ResultRecord import path.

``RunSpec`` says what to run. ``ResultRecord`` (defined in ``core/record.py``)
says what happened; it is re-exported here so plugins keep one stable import
path across the 1.0 -> 0.2 schema change.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from clousight_bench.core.record import ResultRecord

__all__ = ["ResultRecord", "RunSpec", "new_run_id", "utc_now"]


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class RunSpec:
    """What to run: one task from one domain against one provider target.

    ``params`` are task-level overrides; ``target`` is the provider-specific
    config (endpoint / auth reference / region / cluster size ...). Everything
    here reaches the benchmark and environment fingerprints, so never put a raw
    secret in a RunSpec -- reference it by env var name instead.
    """

    domain: str
    task_id: str
    platform: str
    target: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def new_run_id() -> str:
    return f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
