"""T5.2 elasticity / scaling under concurrency.

Drive the runtime at rising concurrency levels and watch where it bends: the
"knee" is the first level at which success rate drops below 1.0 (throttling /
quota) or p95 latency degrades sharply. A runtime that scales cleanly has no
knee within the tested range.

Evidence layer B: method reproducible, numbers environment-dependent. A real
adapter implements ``probe_scaling`` by firing concurrent load and measuring;
local-sim models it deterministically from
``target.scaling = {concurrency_limit, base_ms, overload_penalty_ms}``. If a
platform exposes no scaling probe, ``CapabilityNotSupported`` becomes a finding,
never a crash.
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

LEVELS = [1, 4, 16, 32, 64]  # max 64: meaningful range for a cloud-managed runtime
# p95 more than this multiple of the level-1 baseline counts as a latency knee.
_LATENCY_KNEE_FACTOR = 3.0


class ElasticityTask(Task):
    task_id = "T5.2"
    title = "Elasticity under concurrency"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("capability/elasticity",)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "levels": LEVELS}

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T5.2 needs an AgentRuntimeAdapter")
        return adapter.run_data_plane_probe("scaling", {"levels": LEVELS})

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={"scaling_capability": Measurement(value="unsupported", unit="", evidence="B")},
                findings=[
                    Finding(
                        code="agent_runtime.scaling_probe_absent",
                        severity="info",
                        summary="runtime exposes no scaling probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no scaling probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        points = raw["points"]
        baseline_p95 = points[0]["p95_ms"] if points else 0.0
        knee = None
        for p in points:
            latency_knee = baseline_p95 > 0 and p["p95_ms"] > baseline_p95 * _LATENCY_KNEE_FACTOR
            if p["success_rate"] < 1.0 or latency_knee:
                knee = p["concurrency"]
                break
        peak = points[-1]
        scales_cleanly = knee is None
        findings = []
        if not scales_cleanly:
            findings.append(
                Finding(
                    code="agent_runtime.scaling_knee",
                    severity="warning",
                    summary=f"performance degrades at concurrency {knee}",
                    evidence="B",
                    details={"knee": knee, "peak": peak},
                )
            )
        for msg in raw.get("instance_visibility_findings") or []:
            findings.append(
                Finding(
                    code="agent_runtime.instance_count_not_exposed",
                    severity="info",
                    summary=msg,
                    evidence="B",
                    details={},
                )
            )
        return TaskResult(
            measurements={
                "scaling_capability": Measurement(value="supported", unit="", evidence="B"),
                "scales_cleanly": Measurement(value=scales_cleanly, unit="", evidence="B"),
                "concurrency_knee": Measurement(
                    value=knee if knee is not None else "none", unit="", evidence="B"
                ),
                "max_concurrency_tested": Measurement(value=peak["concurrency"], unit="", evidence="B"),
                "success_rate_at_peak": Measurement(value=peak["success_rate"], unit="", evidence="B"),
                "p95_ms_at_peak": Measurement(value=peak["p95_ms"], unit="ms", evidence="B"),
            },
            findings=findings,
            notes=(
                f"knee={'none' if scales_cleanly else knee}; peak {peak['concurrency']}x "
                f"success={peak['success_rate']} p95={peak['p95_ms']}ms"
            ),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
