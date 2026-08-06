"""T1.4 sustained load / tail latency.

Drive the runtime at a steady target rate and observe what it sustains: the
throughput actually served, the median (p50) and tail (p99) latency, the jitter
(p99-p50 spread, the predictability signal), and the error rate once demand
exceeds what the runtime can hold. Complements T1.1 (single cold/warm start) and
T5.2 (rising concurrency) with steady-state behaviour.

Evidence layer B: method reproducible, numbers environment-dependent. A real
adapter implements ``probe_sustained_load`` by firing real steady traffic and
measuring; local-sim models it deterministically from
``target.load = {sustained_rps, base_ms, tail_ms, error_rate}``. A platform with
no load probe yields an ``unsupported`` measurement, never a crash.
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

DURATION_S = 60.0   # 60s gives ~1000+ samples at 20rps; p99 from <200 samples is statistically invalid
TARGET_RPS = 50.0


class SustainedLoadTask(Task):
    task_id = "T1.4"
    title = "Sustained load & tail latency"
    evidence_layer = "B"
    task_revision = "3"
    scorer_revision = "3"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("performance/sustained-throughput",)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "duration_s": DURATION_S, "target_rps": TARGET_RPS}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.4 needs an AgentRuntimeAdapter")
        return adapter.run_data_plane_probe(
            "sustained_load", {"duration_s": DURATION_S, "target_rps": TARGET_RPS})

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "load_capability": Measurement(
                        value="unsupported", unit="", evidence="B")
                },
                findings=[
                    Finding(
                        code="agent_runtime.load_probe_absent",
                        severity="info",
                        summary="runtime exposes no sustained-load probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no sustained-load probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        error_rate = float(raw["error_rate"])
        transport_err = float(raw.get("transport_error_rate") or 0.0)
        runtime_err = float(raw.get("runtime_error_rate") or 0.0)
        tool_err = float(raw.get("tool_error_rate") or 0.0)
        findings = []
        if transport_err > 0:
            findings.append(
                Finding(
                    code="agent_runtime.load_transport_errors",
                    severity="warning",
                    summary=(
                        f"{transport_err:.1%} of requests failed with transport errors "
                        "(SSL/connection) — indicates connection-pool exhaustion or "
                        "network instability, not runtime errors"
                    ),
                    evidence="B",
                    details={"transport_error_rate": transport_err,
                             "target_rps": raw["target_rps"]},
                )
            )
        if runtime_err > 0:
            findings.append(
                Finding(
                    code="agent_runtime.load_runtime_errors",
                    severity="warning",
                    summary=f"{runtime_err:.1%} of requests failed with runtime errors (AgentRun returned non-2xx)",
                    evidence="B",
                    details={"runtime_error_rate": runtime_err,
                             "target_rps": raw["target_rps"],
                             "throughput_rps": raw["throughput_rps"]},
                )
            )
        if tool_err > 0:
            findings.append(
                Finding(
                    code="agent_runtime.load_tool_errors",
                    severity="info",
                    summary=(
                        f"{tool_err:.1%} of requests had mock-tool failures (AgentRun invoke succeeded, "
                        "downstream tool returned error). This reflects mock-server capacity, not AgentRun rate limiting."
                    ),
                    evidence="B",
                    details={"tool_error_rate": tool_err, "target_rps": raw["target_rps"]},
                )
            )
        if error_rate > 0 and transport_err == 0 and runtime_err == 0 and tool_err == 0:
            # Adapter didn't disaggregate; surface the total.
            findings.append(
                Finding(
                    code="agent_runtime.load_errors",
                    severity="warning",
                    summary=f"{error_rate:.1%} of requests failed under load",
                    evidence="B",
                    details={"error_rate": error_rate, "target_rps": raw["target_rps"],
                             "throughput_rps": raw["throughput_rps"]},
                )
            )
        return TaskResult(
            measurements={
                "load_capability": Measurement(value="supported", unit="", evidence="B"),
                "throughput_rps": Measurement(
                    value=raw["throughput_rps"], unit="rps", evidence="B"),
                "p50_ms": Measurement(value=raw["p50_ms"], unit="ms", evidence="B"),
                "p99_ms": Measurement(value=raw["p99_ms"], unit="ms", evidence="B"),
                "jitter_ms": Measurement(value=raw["jitter_ms"], unit="ms", evidence="B"),
                "error_rate_under_load": Measurement(
                    value=error_rate, unit="", evidence="B"),
                "transport_error_rate": Measurement(
                    value=transport_err, unit="", evidence="B"),
                "runtime_error_rate": Measurement(
                    value=runtime_err, unit="", evidence="B"),
                "tool_error_rate": Measurement(
                    value=tool_err, unit="", evidence="B"),
            },
            findings=findings,
            notes=(f"sustained {raw['throughput_rps']}rps of {raw['target_rps']} target; "
                   f"p50={raw['p50_ms']}ms p99={raw['p99_ms']}ms "
                   f"err={error_rate:.1%} (transport={transport_err:.1%} runtime={runtime_err:.1%} tool={tool_err:.1%})"),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
