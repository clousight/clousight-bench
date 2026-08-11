"""T1.5 warm-pool retention.

How long an idle instance stays warm before the next start pays a cold penalty
again — the keep-alive window a bursty workload cares about. A runtime that keeps
instances warm for minutes avoids repeated cold starts; one that scales to zero
immediately trades cost for cold starts on every gap.

Evidence layer B: method reproducible, numbers environment-dependent. A real
adapter implements ``probe_warm_retention`` by idling then re-invoking and timing
when the cold penalty returns; local-sim reports the configured
``target.warm = {retention_ms, keeps_warm}``. A platform with no retention probe
yields an ``unsupported`` measurement, never a crash.
"""

from __future__ import annotations

from typing import Any

from clousight_bench.core.observation import (
    Finding,
    Measurement,
    ObservationBundle,
    TaskResult,
)
from clousight_bench.core.plugin import ProviderAdapter, Task
from clousight_bench.domains.agent_runtime import permissions as perm
from clousight_bench.domains.agent_runtime.adapters.base import AgentRuntimeAdapter


class WarmRetentionTask(Task):
    task_id = "T1.5"
    title = "Warm-pool retention"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.SESSION_CREATE,)
    capability_tags = ("performance/warm-pool",)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id}

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.5 needs an AgentRuntimeAdapter")
        return adapter.run_data_plane_probe("warm_retention", {})

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "retention_capability": Measurement(value="unsupported", unit="", evidence="B")
                },
                findings=[
                    Finding(
                        code="agent_runtime.retention_probe_absent",
                        severity="info",
                        summary="runtime exposes no warm-retention probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no warm-retention probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        keeps_warm = bool(raw["keeps_warm"])
        findings = []
        if not keeps_warm:
            findings.append(
                Finding(
                    code="agent_runtime.no_warm_pool",
                    severity="info",
                    summary="runtime keeps no warm instance (cold start on every gap)",
                    evidence="B",
                    details={"retention_ms": raw["retention_ms"]},
                )
            )
        return TaskResult(
            measurements={
                "retention_capability": Measurement(value="supported", unit="", evidence="B"),
                "warm_retention_ms": Measurement(value=raw["retention_ms"], unit="ms", evidence="B"),
                "keeps_warm": Measurement(value=keeps_warm, unit="", evidence="B"),
            },
            findings=findings,
            notes=(f"keeps_warm={keeps_warm}; retention={raw['retention_ms']}ms"),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
