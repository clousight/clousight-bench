"""T1.9 time-to-first-token (TTFT).

TTFT is the wall-clock time from sending an invoke request to receiving the
*first* non-empty response chunk. For streaming runtimes this precedes the full
response by the processing time; for non-streaming fallback it equals the full
round-trip latency. Either way it is the latency the end-user perceives before
they see any output.

``execute`` fires SAMPLES streaming invocations and records the TTFT of each.
``score`` reports the distribution (median and p95). A runtime that does not
support streaming probe yields an ``unsupported`` result.

Evidence layer B: the method is reproducible, but the numbers are
environment-dependent (region, load, cold/warm state). The probe warms the
runtime before sampling to avoid attributing cold-start cost to TTFT.
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
from clousight_bench.core.stats import percentiles
from clousight_bench.domains.agent_runtime import permissions as perm
from clousight_bench.domains.agent_runtime.adapters.base import AgentRuntimeAdapter

# One warm-up + 5 measured samples: enough for a stable median and p95.
WARMUP = 1
SAMPLES = 5


class TTFTTask(Task):
    task_id = "T1.9"
    title = "Time-to-first-token (TTFT)"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("performance/ttft",)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "warmup": WARMUP, "samples": SAMPLES}

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.9 needs an AgentRuntimeAdapter")
        return adapter.run_data_plane_probe("ttft", {"warmup": WARMUP, "samples": SAMPLES})

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={"ttft_capability": Measurement(value="unsupported", unit="", evidence="B")},
                findings=[
                    Finding(
                        code="agent_runtime.ttft_probe_absent",
                        severity="info",
                        summary="runtime exposes no TTFT probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no TTFT probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        ttft_ms = list(raw.get("ttft_ms") or [])
        p = percentiles(ttft_ms)
        p50, p95 = p[50], p[95]
        # If all values are 0, the streaming agent was not deployed yet (fallback path).
        all_zero = all(v == 0.0 for v in ttft_ms)
        findings: list[Finding] = []
        if all_zero:
            findings.append(
                Finding(
                    code="agent_runtime.ttft_fallback_rtt",
                    severity="info",
                    summary=(
                        "TTFT returned 0.0ms for all samples; the deployed agent may not "
                        "support streaming. Redeploy the benchmark agent (contains SSE "
                        "support) to measure true TTFT."
                    ),
                    evidence="B",
                    details={"samples": len(ttft_ms)},
                )
            )
        return TaskResult(
            measurements={
                "ttft_capability": Measurement(value="supported", unit="", evidence="B"),
                "ttft_p50_ms": Measurement(
                    value=p50, unit="ms", evidence="B", aggregation="p50", sample_count=len(ttft_ms)
                ),
                "ttft_p95_ms": Measurement(
                    value=p95, unit="ms", evidence="B", aggregation="p95", sample_count=len(ttft_ms)
                ),
                # ttft_p50/p95 are warm steady-state; the ~86s cold start is
                # reported separately (None on local-sim / older non-two-dim probe).
                "cold_start_ms": Measurement(value=raw.get("cold_start_ms"), unit="ms", evidence="B"),
                "warm_reliable": Measurement(value=bool(raw.get("warm_reliable", False)), unit="", evidence="B"),
            },
            findings=findings,
            notes=(
                f"TTFT p50={p50}ms p95={p95}ms (n={len(ttft_ms)}"
                + (" — streaming fallback, redeploy agent" if all_zero else "")
                + ")"
            ),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
