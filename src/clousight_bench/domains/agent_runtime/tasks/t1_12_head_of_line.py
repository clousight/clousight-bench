"""T1.12 head-of-line blocking.

Does a slow request in a session queue block fast requests sharing the same
session? We fire 1 slow request (reports endpoint) and 5 fast requests (prices
endpoint) concurrently on the same session_id and compare latencies.

If the fast requests' p99 exceeds half the slow request's latency, the runtime
has head-of-line blocking in its per-session dispatch queue.

Evidence layer C: deterministic, replayable.
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

# HOL is flagged when fast_p99 > slow_p50 * this ratio.
HOL_THRESHOLD = 0.5


class HOLBlockingTask(Task):
    task_id = "T1.12"
    title = "Head-of-line blocking"
    evidence_layer = "C"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("reliability/hol-blocking",)
    task_revision = "1"
    scorer_revision = "1"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "slow_endpoint": "reports",
            "fast_endpoint": "prices",
            "fast_count": 5,
            "hol_threshold": HOL_THRESHOLD,
        }

    def environment_facts(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> dict[str, Any]:
        return {}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.12 needs an AgentRuntimeAdapter")
        return adapter.run_data_plane_probe("hol_blocking", {})

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        blocked = bool(raw.get("blocked"))
        fast_p50_ms = float(raw.get("fast_p50_ms", 0.0))
        slow_p50_ms = float(raw.get("slow_p50_ms", 0.0))
        hol_ratio = float(raw.get("hol_ratio", 0.0))

        findings: list[Finding] = []
        if blocked:
            findings.append(
                Finding(
                    code="agent_runtime.hol_blocking",
                    severity="warning",
                    summary="slow request delayed fast requests sharing the same session queue",
                    evidence="C",
                    details={
                        "fast_p50_ms": fast_p50_ms,
                        "slow_p50_ms": slow_p50_ms,
                        "hol_ratio": hol_ratio,
                        "threshold": HOL_THRESHOLD,
                    },
                )
            )

        return TaskResult(
            measurements={
                "blocked": Measurement(value=blocked, unit="", evidence="C"),
                "fast_p50_ms": Measurement(
                    value=fast_p50_ms, unit="ms", evidence="C", aggregation="p50"
                ),
                "slow_p50_ms": Measurement(
                    value=slow_p50_ms, unit="ms", evidence="C", aggregation="p50"
                ),
                "hol_ratio": Measurement(value=hol_ratio, unit="ratio", evidence="C"),
            },
            findings=findings,
            notes=f"HOL probe: fast_p50={fast_p50_ms:.1f}ms slow_p50={slow_p50_ms:.1f}ms ratio={hol_ratio:.3f} blocked={blocked}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
