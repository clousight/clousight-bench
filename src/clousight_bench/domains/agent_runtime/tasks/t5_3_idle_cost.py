"""T5.3 idle / scale-to-zero cost.

T5.1 attributes the cost of *work done*; this observes the cost of *doing
nothing*. A runtime that scales to zero bills nothing while idle; one that keeps
a warm instance bills for it. For bursty workloads the idle bill dominates.

Evidence layer B: method reproducible, numbers environment-dependent. A real
adapter idles an instance and reads the meter; local-sim reports the configured
``target.idle = {scales_to_zero, cost_per_hour}``. No probe -> ``unsupported``.
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


class IdleCostTask(Task):
    task_id = "T5.3"
    title = "Idle / scale-to-zero cost"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.SESSION_CREATE,)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T5.3 needs an AgentRuntimeAdapter")
        try:
            r = adapter.probe_idle_cost()
        except CapabilityNotSupported as exc:
            return ObservationBundle(
                observations={"capability": "unsupported", "reason": str(exc)}
            )
        return ObservationBundle(
            observations={
                "capability": "supported",
                "scales_to_zero": r.scales_to_zero,
                "idle_cost_per_hour": r.idle_cost_per_hour,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "idle_cost_capability": Measurement(
                        value="unsupported", unit="", evidence="B")
                },
                findings=[
                    Finding(
                        code="agent_runtime.idle_cost_probe_absent",
                        severity="info",
                        summary="runtime exposes no idle-cost probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no idle-cost probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        scales_to_zero = bool(raw["scales_to_zero"])
        findings = []
        if not scales_to_zero:
            findings.append(Finding(
                code="agent_runtime.no_scale_to_zero", severity="info",
                summary="runtime bills while idle (no scale-to-zero)", evidence="B",
                details={"idle_cost_per_hour": raw["idle_cost_per_hour"]}))
        return TaskResult(
            measurements={
                "idle_cost_capability": Measurement(
                    value="supported", unit="", evidence="B"),
                "scales_to_zero": Measurement(
                    value=scales_to_zero, unit="", evidence="B"),
                "idle_cost_per_hour": Measurement(
                    value=raw["idle_cost_per_hour"], unit="USD/h", evidence="B"),
            },
            findings=findings,
            notes=(f"scales_to_zero={scales_to_zero} "
                   f"idle_cost_per_hour={raw['idle_cost_per_hour']}"),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
