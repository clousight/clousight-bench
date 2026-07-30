"""T0.2 teardown cleanliness.

When a runtime instance is deleted, does the platform actually reclaim
everything, or does it leave resources behind (a leaked endpoint / version /
sandbox that keeps costing money)? ``execute`` provisions an instance, tears it
down, and records whether teardown was clean plus any residual resource ids.
``score`` turns that into measurements; residue is a warning finding.

Evidence layer C: deterministic. On mock, ``target.provision.clean_teardown``
and ``target.provision.residual_on_delete`` drive both the clean and the leaky
case so scoring is exercisable with no account.
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


class TeardownCleanlinessTask(Task):
    task_id = "T0.2"
    title = "Teardown cleanliness"
    evidence_layer = "C"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.PROVISION, perm.DEPROVISION)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T0.2 needs an AgentRuntimeAdapter")
        spec = {
            "artifact_ref": str(
                adapter.target.get("artifact_ref") or adapter.target.get("agent_id") or ""
            )
        }
        try:
            provisioned = adapter.provision(spec)
            result = adapter.deprovision(provisioned.runtime_id)
        except CapabilityNotSupported as exc:
            return ObservationBundle(
                observations={"capability": "unsupported", "reason": str(exc)}
            )
        return ObservationBundle(
            observations={
                "capability": "supported",
                "teardown_ms": result.teardown_ms,
                "clean": result.clean,
                "residual": list(result.residual),
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "teardown_capability": Measurement(
                        value="unsupported", unit="", evidence="C"
                    ),
                },
                findings=[
                    Finding(
                        code="agent_runtime.provision_api_absent",
                        severity="info",
                        summary="runtime exposes no provisioning/teardown API",
                        evidence="C",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no teardown API",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        clean = bool(raw.get("clean"))
        residual = list(raw.get("residual", []))
        findings = (
            []
            if clean
            else [
                Finding(
                    code="agent_runtime.teardown_residual",
                    severity="warning",
                    summary="teardown left residual resources behind (potential cost leak)",
                    evidence="C",
                    details={"residual": residual},
                )
            ]
        )
        return TaskResult(
            measurements={
                "teardown_ms": Measurement(
                    value=raw.get("teardown_ms"), unit="ms", evidence="C"
                ),
                "teardown_clean": Measurement(value=clean, unit="", evidence="C"),
                "residual_count": Measurement(
                    value=len(residual), unit="", evidence="C"
                ),
            },
            findings=findings,
            notes=f"teardown clean={clean}, residual={len(residual)}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
