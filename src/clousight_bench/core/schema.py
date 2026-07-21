"""Unified result schema + reproducibility helpers.

Every benchmark run emits a ResultRecord carrying config_hash + runner_version +
evidence_layer, so any reader can tell exactly which configuration produced a
number and how trustworthy it is. This is the reproducibility contract shared by
ALL domains (agent runtimes, big data clusters, databases, compute, messaging).

Evidence layers:
    A - authoritative documentation / vendor statements (read, not measured)
    B - environment observation (method reproducible, numbers environment-dependent)
    C - controlled-variable measurement (precisely reproducible; challenge us)
    D - marketing material (never used as load-bearing evidence)
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from clousight_bench import RUNNER_VERSION

EVIDENCE_LAYERS = {"A", "B", "C", "D"}


def config_hash(config: dict[str, Any]) -> str:
    """Deterministic hash of everything that determines a result."""
    blob = json.dumps(config, ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class RunSpec:
    """What to run: one task from one domain against one provider target.

    ``params`` are task-level overrides; ``target`` is the provider-specific
    config (endpoint / auth reference / region / cluster size ...). Everything
    here is hashed into config_hash, so never put raw secrets in a RunSpec --
    reference them by env var name instead.
    """

    domain: str
    task_id: str
    platform: str
    target: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResultRecord:
    """One benchmark result. The only shape the report layer understands."""

    domain: str
    task_id: str
    platform: str
    run_id: str
    started_at: str
    finished_at: str
    config_hash: str
    evidence_layer: str
    metrics: dict[str, Any]
    ok: bool = True
    runner_version: str = RUNNER_VERSION
    raw: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    error: str | None = None

    def __post_init__(self) -> None:
        if self.evidence_layer not in EVIDENCE_LAYERS:
            raise ValueError(
                f"evidence_layer must be one of {sorted(EVIDENCE_LAYERS)}, got {self.evidence_layer!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResultRecord:
        return cls(**data)


def new_run_id() -> str:
    return f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
