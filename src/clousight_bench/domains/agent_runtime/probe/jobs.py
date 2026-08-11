"""Job data model for the probe protocol: what csbench sends, what the probe
reports back. Pure data + serialization; no I/O, no cloud."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from clousight_bench.core.observation import ObservationBundle

JOB_STATUSES: tuple[str, ...] = ("pending", "running", "completed", "failed")
TERMINAL_STATUSES: tuple[str, ...] = ("completed", "failed")


@dataclass
class JobSpec:
    """One data-plane probe run, dispatched from csbench to the probe."""

    probe: str
    params: dict[str, Any]
    target_endpoint: str
    mock_base_url: str = ""
    mock_token: str = ""
    session_header_scheme: str = "X-AgentRun-Session-ID"
    oss_prefix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe": self.probe,
            "params": dict(self.params),
            "target_endpoint": self.target_endpoint,
            "mock_base_url": self.mock_base_url,
            "mock_token": self.mock_token,
            "session_header_scheme": self.session_header_scheme,
            "oss_prefix": self.oss_prefix,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JobSpec:
        return cls(
            probe=str(d["probe"]),
            params=dict(d.get("params") or {}),
            target_endpoint=str(d["target_endpoint"]),
            mock_base_url=str(d.get("mock_base_url", "")),
            mock_token=str(d.get("mock_token", "")),
            session_header_scheme=str(d.get("session_header_scheme", "X-AgentRun-Session-ID")),
            oss_prefix=str(d.get("oss_prefix", "")),
        )


@dataclass
class JobProgress:
    """A probe's self-reported progress; each probe fills it in its own terms."""

    phase: str = "pending"
    completed: int = 0
    total: int = 0
    elapsed_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "completed": self.completed,
            "total": self.total,
            "elapsed_s": self.elapsed_s,
        }


@dataclass
class JobRecord:
    """The full state of a job as the probe holds and serves it."""

    job_id: str
    status: str = "pending"
    progress: JobProgress = field(default_factory=JobProgress)
    live_metrics: dict[str, Any] = field(default_factory=dict)
    observations: dict[str, Any] | None = None  # ObservationBundle.to_dict() when terminal
    error: str | None = None
    chunk_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "progress": self.progress.to_dict(),
            "live_metrics": dict(self.live_metrics),
            "observations": self.observations,
            "error": self.error,
            "chunk_refs": list(self.chunk_refs),
        }


def new_job_id() -> str:
    return f"job-{uuid.uuid4().hex[:12]}"


def observation_bundle_from_dict(d: dict[str, Any]) -> ObservationBundle:
    """Rebuild an ObservationBundle from its ``to_dict()`` projection."""
    return ObservationBundle(
        observations=dict(d.get("observations") or {}),
        series=dict(d.get("series") or {}),
        artifacts=list(d.get("artifacts") or []),
    )
