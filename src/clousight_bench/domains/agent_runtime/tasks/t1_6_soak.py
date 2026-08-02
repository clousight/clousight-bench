"""T1.6 soak availability.

Run steady traffic over a window and observe steady-state availability + error
rate — the reliability signal a single-shot latency test misses. Complements
T1.3 (recover from ONE injected fault) with sustained "how often does it just
work" behaviour.

Evidence layer B: method reproducible, numbers environment-dependent. A real
adapter runs continuous traffic and measures; local-sim reports the configured
``target.soak = {availability, error_rate, rps}``. A platform with no soak probe
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
from clousight_bench.domains.agent_runtime.adapters.base import (
    AgentRuntimeAdapter,
    CapabilityNotSupported,
)

DURATION_S = 10.0
# below this steady-state availability, flag the runtime (a common 3-nines bar).
_AVAILABILITY_SLA = 0.999


class SoakTask(Task):
    task_id = "T1.6"
    title = "Soak availability"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("reliability/soak",)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "duration_s": DURATION_S}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.6 needs an AgentRuntimeAdapter")
        try:
            r = adapter.probe_soak(DURATION_S)
        except CapabilityNotSupported as exc:
            return ObservationBundle(
                observations={"capability": "unsupported", "reason": str(exc)}
            )
        return ObservationBundle(
            observations={
                "capability": "supported",
                "availability": r.availability,
                "error_rate": r.error_rate,
                "requests": r.requests,
                "window_s": r.window_s,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "soak_capability": Measurement(
                        value="unsupported", unit="", evidence="B")
                },
                findings=[
                    Finding(
                        code="agent_runtime.soak_probe_absent",
                        severity="info",
                        summary="runtime exposes no soak probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no soak probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        availability = float(raw["availability"])
        findings = []
        if availability < _AVAILABILITY_SLA:
            findings.append(
                Finding(
                    code="agent_runtime.availability_below_sla",
                    severity="warning",
                    summary=f"availability {availability:.3%} under soak",
                    evidence="B",
                    details={"availability": availability, "sla": _AVAILABILITY_SLA,
                             "error_rate": raw["error_rate"]},
                )
            )
        return TaskResult(
            measurements={
                "soak_capability": Measurement(value="supported", unit="", evidence="B"),
                "availability": Measurement(value=availability, unit="", evidence="B"),
                "soak_error_rate": Measurement(
                    value=raw["error_rate"], unit="", evidence="B"),
                "soak_requests": Measurement(value=raw["requests"], unit="", evidence="B"),
            },
            findings=findings,
            notes=(f"availability={availability:.3%} err={raw['error_rate']:.3%} "
                   f"over {raw['requests']} req / {raw['window_s']}s"),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
