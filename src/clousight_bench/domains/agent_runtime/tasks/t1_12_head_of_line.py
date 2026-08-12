"""T1.12 head-of-line blocking (v2).

Two-phase measurement:
  Phase A (baseline): N concurrent fast requests with no slow injected.
  Phase B (under-slow): 1 slow (real injected latency) + N fast concurrent
      on the same session.

serialized=True means the platform's session queue head-of-line blocks fast
requests when a slow one is queued ahead — a session-layer serialisation signal.

Evidence layer B: real latency injection + mock-server-counted correlation buckets.
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

# HOL is flagged when fast_p50_under_slow > fast_p50_baseline * this multiplier.
HOL_THRESHOLD = 2.0


class HOLBlockingTask(Task):
    task_id = "T1.12"
    title = "Head-of-line blocking"
    evidence_layer = "B"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("reliability/hol-blocking",)
    task_revision = "2"
    scorer_revision = "2"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "slow_endpoint": "reports",
            "fast_endpoint": "prices",
            "fast_count": 20,
            "slow_latency_ms": 500,
            "hol_threshold": HOL_THRESHOLD,
        }

    def environment_facts(self, adapter: ProviderAdapter, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.12 needs an AgentRuntimeAdapter")
        return adapter.run_data_plane_probe("hol_blocking", {})

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        serialized = bool(raw.get("serialized", False))
        fast_p50_baseline = float(raw.get("fast_p50_baseline", 0.0))
        fast_p50_under_slow = float(raw.get("fast_p50_under_slow", 0.0))
        hol_ratio = float(raw.get("hol_ratio", 0.0))

        findings: list[Finding] = []
        if serialized:
            findings.append(
                Finding(
                    code="agent_runtime.hol_blocking",
                    severity="warning",
                    summary="平台会话层队头阻塞: fast requests delayed ≥2× vs baseline",
                    evidence="B",
                    details={
                        "fast_p50_baseline_ms": fast_p50_baseline,
                        "fast_p50_under_slow_ms": fast_p50_under_slow,
                        "hol_ratio": hol_ratio,
                        "threshold": HOL_THRESHOLD,
                    },
                )
            )

        return TaskResult(
            measurements={
                "hol_capability": Measurement(value="supported", unit="", evidence="B"),
                "fast_p50_baseline": Measurement(
                    value=fast_p50_baseline, unit="ms", evidence="B", aggregation="p50"
                ),
                "fast_p50_under_slow": Measurement(
                    value=fast_p50_under_slow, unit="ms", evidence="B", aggregation="p50"
                ),
                "hol_ratio": Measurement(value=hol_ratio, unit="ratio", evidence="B"),
                "serialized": Measurement(value=serialized, unit="", evidence="B"),
            },
            findings=findings,
            notes=(
                f"HOL probe: baseline_p50={fast_p50_baseline:.1f}ms "
                f"under_slow_p50={fast_p50_under_slow:.1f}ms "
                f"ratio={hol_ratio:.3f} serialized={serialized}"
            ),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
