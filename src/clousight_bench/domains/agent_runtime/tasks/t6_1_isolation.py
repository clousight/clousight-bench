"""T6.1 tenant isolation / sandbox strength.

A managed runtime hosts untrusted code from many tenants. Observe three isolation
guarantees: tenant separation, default-deny network egress, and a private
ephemeral filesystem. Weak isolation is a security hazard regardless of speed.

Evidence layers:
  B — the boundary was actively probed during this run (cross-session access
      attempt, live egress test, filesystem read).
  A — the platform's documentation asserts the property; the benchmark did not
      independently verify it. The adapter sets ``IsolationResult.platform_asserted_dimensions``
      to list these. ``measured_score`` counts only B-evidence dimensions.

No probe -> ``unsupported``.
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
    task_revision = "2"
    scorer_revision = "2"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("capability/isolation",)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id}

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T6.1 needs an AgentRuntimeAdapter")
        try:
            r = adapter.probe_isolation()
        except CapabilityNotSupported as exc:
            return ObservationBundle(observations={"capability": "unsupported", "reason": str(exc)})
        asserted: list[str] = list(getattr(r, "platform_asserted_dimensions", []) or [])
        return ObservationBundle(
            observations={
                "capability": "supported",
                "tenant_isolated": r.tenant_isolated,
                "network_egress_controlled": r.network_egress_controlled,
                "filesystem_isolated": r.filesystem_isolated,
                "platform_asserted_dimensions": asserted,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "isolation_capability": Measurement(value="unsupported", unit="", evidence="B")
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
        asserted: list[str] = list(raw.get("platform_asserted_dimensions") or [])
        _DIM_MAP = {
            "tenant_isolated": tenant,
            "network_egress_controlled": egress,
            "filesystem_isolated": fs,
        }
        findings = []
        if asserted:
            findings.append(
                Finding(
                    code="agent_runtime.isolation_platform_asserted",
                    severity="info",
                    summary=(
                        f"dimensions {asserted} are platform documentation claims, "
                        "not live measurements (evidence A). "
                        "Active probing would require agent-side instrumentation."
                    ),
                    evidence="A",
                    details={"asserted": asserted},
                )
            )
        # measured_score counts only dimensions with live probe evidence (B).
        measured_dims = [k for k in _DIM_MAP if k not in asserted]
        measured_score = sum(_DIM_MAP[k] for k in measured_dims)
        weak = [k for k, ok in _DIM_MAP.items() if not ok]
        if weak:
            findings.append(
                Finding(
                    code="agent_runtime.weak_isolation",
                    severity="warning",
                    summary=f"weak isolation: {', '.join(weak)}",
                    evidence="B",
                    details={"weak": weak},
                )
            )

        def _ev(dim: str) -> str:
            return "A" if dim in asserted else "B"

        return TaskResult(
            measurements={
                "isolation_capability": Measurement(value="supported", unit="", evidence="B"),
                "tenant_isolated": Measurement(value=tenant, unit="", evidence=_ev("tenant_isolated")),
                "network_egress_controlled": Measurement(
                    value=egress, unit="", evidence=_ev("network_egress_controlled")
                ),
                "filesystem_isolated": Measurement(value=fs, unit="", evidence=_ev("filesystem_isolated")),
                "measured_score": Measurement(
                    value=measured_score, unit=f"/{len(measured_dims)}", evidence="B"
                ),
                "asserted_score": Measurement(
                    value=sum(_DIM_MAP[k] for k in asserted if k in _DIM_MAP),
                    unit=f"/{len(asserted)}",
                    evidence="A",
                ),
            },
            findings=findings,
            notes=(
                f"measured={measured_score}/{len(measured_dims)} "
                f"asserted={len(asserted)}/3 "
                f"tenant={tenant} egress={egress} fs={fs}"
            ),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
