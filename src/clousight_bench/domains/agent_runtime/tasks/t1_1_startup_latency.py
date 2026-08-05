"""T1.1 cold/warm start latency.

How long from asking for a session to having one? The first session on a fresh
runtime pays a cold-start cost (container / sandbox spin-up); later ones are
warm (reuse). ``execute`` times ``create_session`` for one cold session and
several warm ones; ``score`` turns those raw timings into distributions plus
the cold/warm ratio.

**Platform note**: some managed runtimes (e.g. AgentRun) use client-side UUID
session IDs — ``create_session`` is a local operation (~0ms). Their real
cold-start cost is in provisioning (T0.1). When
``adapter.session_cold_start_is_provision`` is True, T1.1 still measures the
``create_session`` latency honestly (it IS zero-cost), but the score carries a
``cold_start_via_provision`` flag so readers know where to look for the cold
penalty.

Evidence layer B: the method is reproducible, but the numbers are
environment-dependent (region, network, load). On local-sim the penalty is a
deterministic knob (``target.startup = {cold_ms, warm_ms}``) so scoring can be
exercised on a fast and a slow-cold-start runtime with no account.
"""
from __future__ import annotations

import time
from typing import Any

from clousight_bench.core.observation import Finding, Measurement, ObservationBundle, TaskResult
from clousight_bench.core.plugin import ProviderAdapter, Task
from clousight_bench.core.stats import percentiles
from clousight_bench.domains.agent_runtime import permissions as perm
from clousight_bench.domains.agent_runtime.adapters.base import AgentRuntimeAdapter

WARM_SAMPLES = 5


def _timed(fn) -> tuple[Any, float]:
    start = time.perf_counter()
    result = fn()
    return result, round((time.perf_counter() - start) * 1000, 2)


class StartupLatencyTask(Task):
    task_id = "T1.1"
    title = "Cold/warm start latency"
    evidence_layer = "B"
    task_revision = "2"
    scorer_revision = "2"
    required_permissions = (perm.SESSION_CREATE,)
    capability_tags = ("performance/cold-start", "performance/warm-start")
    # create_session / destroy_session don't invoke the agent — no mock server needed.
    # Adapters where session creation IS a runtime call should override this.
    requires_mock_server = False

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "warm_samples": WARM_SAMPLES}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.1 needs an AgentRuntimeAdapter")
        cold_start_via_provision = bool(
            getattr(adapter, "session_cold_start_is_provision", False)
        )
        # first session -> cold (runtime spin-up); the rest -> warm (reuse)
        cold_session, cold_ms = _timed(adapter.create_session)
        adapter.destroy_session(cold_session)
        warm_ms: list[float] = []
        for _ in range(WARM_SAMPLES):
            session, dt = _timed(adapter.create_session)
            warm_ms.append(dt)
            adapter.destroy_session(session)
        return ObservationBundle(
            observations={
                "cold_ms": cold_ms,
                "warm_ms": warm_ms,
                "cold_start_via_provision": cold_start_via_provision,
            },
            series={"warm_start_ms": [[i, v] for i, v in enumerate(warm_ms, start=1)]},
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        cold_ms = raw["cold_ms"]
        warm_ms = list(raw.get("warm_ms", []))
        cold_start_via_provision = bool(raw.get("cold_start_via_provision", False))
        warm_p = percentiles(warm_ms)
        warm_p50, warm_p95 = warm_p[50], warm_p[95]
        ratio = round(cold_ms / warm_p50, 2) if warm_p50 > 0 else None
        warm_measurements: dict = {
            "warm_start_p50_ms": Measurement(
                value=warm_p50, unit="ms", evidence="B",
                aggregation="p50", sample_count=len(warm_ms)),
            "warm_start_p95_ms": Measurement(
                value=warm_p95, unit="ms", evidence="B",
                aggregation="p95", sample_count=len(warm_ms)),
        }
        findings: list[Finding] = []
        if cold_start_via_provision:
            # create_session is a local UUID operation on this platform.
            # The cold-start cost (container spin-up) is in T0.1 (provision latency).
            measurements: dict = {
                "session_create_ms": Measurement(
                    value=cold_ms, unit="ms", evidence="B"),
                "cold_start_via_provision": Measurement(
                    value=True, unit="", evidence="B"),
                **warm_measurements,
            }
            findings.append(Finding(
                code="agent_runtime.cold_start_at_provision",
                severity="info",
                summary=(
                    "session creation is a local operation (~0ms); real cold-start "
                    "cost is at provision (T0.1). cold_warm_ratio is not meaningful here."
                ),
                evidence="B",
                details={"session_create_ms": cold_ms},
            ))
            notes = (
                f"session_create={cold_ms}ms (client-local UUID); "
                f"warm_p50={warm_p50}ms; cold-start cost is in T0.1 (provision)"
            )
        else:
            measurements = {
                "cold_start_ms": Measurement(value=cold_ms, unit="ms", evidence="B"),
                "cold_warm_ratio": Measurement(value=ratio, unit="", evidence="B"),
                **warm_measurements,
            }
            notes = f"cold={cold_ms}ms vs warm_p50={warm_p50}ms (ratio={ratio})"
        return TaskResult(
            measurements=measurements,
            findings=findings,
            notes=notes,
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
