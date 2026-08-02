"""T6.1 tenant isolation / sandbox strength.

A managed runtime hosts untrusted code from many tenants. Observe three isolation
guarantees: tenant separation, default-deny network egress, and a private
ephemeral filesystem. Weak isolation is a security hazard regardless of speed.

Evidence layer B: method reproducible, numbers environment-dependent. A real
adapter probes each boundary (attempt cross-tenant access, egress, fs peek);
local-sim reports the configured ``target.isolation``. No probe -> ``unsupported``.
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


class IsolationTask(Task):
    task_id = "T6.1"
    title = "Tenant isolation"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("capability/isolation",)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T6.1 needs an AgentRuntimeAdapter")
        try:
            r = adapter.probe_isolation()
        except CapabilityNotSupported as exc:
            return ObservationBundle(
                observations={"capability": "unsupported", "reason": str(exc)}
            )
        return ObservationBundle(
            observations={
                "capability": "supported",
                "tenant_isolated": r.tenant_isolated,
                "network_egress_controlled": r.network_egress_controlled,
                "filesystem_isolated": r.filesystem_isolated,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "isolation_capability": Measurement(
                        value="unsupported", unit="", evidence="B")
                },
                findings=[
                    Finding(
                        code="agent_runtime.isolation_probe_absent",
                        severity="info",
                        summary="runtime exposes no isolation probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no isolation probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        tenant = bool(raw["tenant_isolated"])
        egress = bool(raw["network_egress_controlled"])
        fs = bool(raw["filesystem_isolated"])
        score = sum((tenant, egress, fs))
        findings = []
        if score < 3:
            weak = [n for n, ok in (("tenant", tenant), ("network_egress", egress),
                                    ("filesystem", fs)) if not ok]
            findings.append(Finding(
                code="agent_runtime.weak_isolation", severity="warning",
                summary=f"weak isolation: {', '.join(weak)}", evidence="B",
                details={"weak": weak}))
        return TaskResult(
            measurements={
                "isolation_capability": Measurement(
                    value="supported", unit="", evidence="B"),
                "tenant_isolated": Measurement(value=tenant, unit="", evidence="B"),
                "network_egress_controlled": Measurement(
                    value=egress, unit="", evidence="B"),
                "filesystem_isolated": Measurement(value=fs, unit="", evidence="B"),
                "isolation_score": Measurement(value=score, unit="/3", evidence="B"),
            },
            findings=findings,
            notes=f"isolation_score={score}/3 tenant={tenant} egress={egress} fs={fs}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
