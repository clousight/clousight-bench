"""T4.5 telemetry export latency.

Observability is only useful if telemetry lands quickly and completely. Observe
how long a span takes to become visible in the backend (emit -> queryable) and
what fraction is dropped on export — high latency or drops blind on-call.

Evidence layer B: method reproducible, numbers environment-dependent. A real
adapter emits and polls the backend; local-sim reports the configured
``target.export``. No probe -> ``unsupported``, never a crash.
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
from clousight_bench.domains.agent_runtime.adapters.base import (
    AgentRuntimeAdapter,
    CapabilityNotSupported,
)


class ExportLatencyTask(Task):
    task_id = "T4.5"
    title = "Export latency"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("observability/export-latency",)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T4.5 needs an AgentRuntimeAdapter")
        try:
            r = adapter.probe_export_latency()
        except CapabilityNotSupported as exc:
            return ObservationBundle(
                observations={"capability": "unsupported", "reason": str(exc)}
            )
        return ObservationBundle(
            observations={
                "capability": "supported",
                "export_latency_ms": r.export_latency_ms,
                "dropped_ratio": r.dropped_ratio,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "export_capability": Measurement(
                        value="unsupported", unit="", evidence="B")
                },
                findings=[
                    Finding(
                        code="agent_runtime.export_probe_absent",
                        severity="info",
                        summary="runtime exposes no export-latency probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no export-latency probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        dropped = float(raw["dropped_ratio"])
        findings = []
        if dropped > 0:
            findings.append(Finding(
                code="agent_runtime.telemetry_dropped", severity="warning",
                summary=f"{dropped:.1%} of telemetry dropped on export", evidence="B",
                details={"dropped_ratio": dropped}))
        return TaskResult(
            measurements={
                "export_capability": Measurement(value="supported", unit="", evidence="B"),
                "export_latency_ms": Measurement(
                    value=raw["export_latency_ms"], unit="ms", evidence="B"),
                "dropped_ratio": Measurement(value=dropped, unit="", evidence="B"),
            },
            findings=findings,
            notes=f"export_latency={raw['export_latency_ms']}ms dropped={dropped:.1%}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
