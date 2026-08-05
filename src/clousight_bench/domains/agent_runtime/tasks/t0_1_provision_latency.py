"""T0.1 provisioning (deploy) latency.

How long from asking the platform to stand up a runtime instance to that
instance being ready to serve? ``execute`` provisions SAMPLES instances from the
benchmark artifact (each torn down immediately) and records the distribution of
create->ready latency. ``score`` reports the full distribution (p25/median/p75/stdev)
so a single slow image-pull or capacity-constrained event does not dominate the result.

**Sampling design:** SAMPLES=7 gives enough points for p25/p75 estimation. Between
each pair of samples the harness idles for INTER_SAMPLE_IDLE_S=60 to let any
warm-container cache expire, so each measurement hits a true cold-deploy path.
Total wall-clock ≈ 7 × (30–120s per provision) + 6 × 60s ≈ 10–25 min. Intentional.

Evidence layer B: the method is reproducible, but the numbers are
environment-dependent (region, image pull, cold capacity). On mock the cost is
a deterministic knob (``target.provision.ready_ms``) so scoring can be exercised
with no account.
"""
from __future__ import annotations

import contextlib
import statistics
import time
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
from clousight_bench.domains.agent_runtime.adapters.base import (
    AgentRuntimeAdapter,
    CapabilityNotSupported,
)

# 7 samples: enough for p25/p75 estimation; each separated by 60s to defeat
# container cache reuse so each measurement is a true cold provision.
SAMPLES = 7
INTER_SAMPLE_IDLE_S = 60


class ProvisionLatencyTask(Task):
    task_id = "T0.1"
    title = "Provisioning (deploy) latency"
    evidence_layer = "B"
    task_revision = "3"
    scorer_revision = "3"
    required_permissions = (perm.PROVISION, perm.DEPROVISION)
    capability_tags = ("performance/provisioning",)
    requires_mock_server = False  # control-plane only: no tool-call mock needed

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "samples": SAMPLES,
                "inter_sample_idle_s": INTER_SAMPLE_IDLE_S}

    def _artifact_spec(self, adapter: AgentRuntimeAdapter) -> dict[str, Any]:
        return {
            "artifact_ref": str(
                adapter.target.get("artifact_ref") or adapter.target.get("agent_id") or ""
            )
        }

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T0.1 needs an AgentRuntimeAdapter")
        latencies: list[float] = []
        ready_all: list[bool] = []
        artifact_ref = ""
        for i in range(SAMPLES):
            try:
                spec = self._artifact_spec(adapter)
                spec["_sample"] = i  # disambiguates runtime names across samples
                result = adapter.provision(spec)
            except CapabilityNotSupported as exc:
                return ObservationBundle(
                    observations={"capability": "unsupported", "reason": str(exc)}
                )
            latencies.append(result.ready_latency_ms)
            ready_all.append(result.ready)
            artifact_ref = result.artifact_ref
            # Tear down immediately; teardown failure is T0.2's concern.
            with contextlib.suppress(Exception):
                adapter.deprovision(result.runtime_id)
            # Inter-sample idle: let any warm-container cache expire before the next
            # provision so each measurement is a genuine cold-deploy path.
            if i < SAMPLES - 1:
                time.sleep(INTER_SAMPLE_IDLE_S)
        return ObservationBundle(
            observations={
                "capability": "supported",
                "latencies_ms": latencies,
                "ready_all": ready_all,
                "artifact_ref": artifact_ref,
            },
            series={"provision_ready_ms": [[i + 1, ms] for i, ms in enumerate(latencies)]},
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "provision_capability": Measurement(
                        value="unsupported", unit="", evidence="B"
                    ),
                },
                findings=[
                    Finding(
                        code="agent_runtime.provision_api_absent",
                        severity="info",
                        summary="runtime exposes no provisioning (deploy) API",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no provisioning API",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        latencies = list(raw.get("latencies_ms") or [])
        ready_all = list(raw.get("ready_all") or [True])
        if not latencies:
            latencies = [0.0]
        p = percentiles(latencies, ps=(25, 50, 75, 95, 99))
        median_ms = round(p[50], 2)
        p25_ms = round(p[25], 2)
        p75_ms = round(p[75], 2)
        stdev_ms = round(statistics.stdev(latencies), 2) if len(latencies) > 1 else 0.0
        ready = all(ready_all)
        findings = (
            []
            if ready
            else [
                Finding(
                    code="agent_runtime.provision_not_ready",
                    severity="warning",
                    summary="one or more provisioned runtimes did not reach a ready state",
                    evidence="B",
                    details={"ready_all": ready_all},
                )
            ]
        )
        return TaskResult(
            measurements={
                "provision_ready_ms": Measurement(
                    value=median_ms, unit="ms", evidence="B",
                    aggregation="p50", sample_count=len(latencies)),
                "provision_ready_ms_p25": Measurement(
                    value=p25_ms, unit="ms", evidence="B",
                    aggregation="p25", sample_count=len(latencies)),
                "provision_ready_ms_p75": Measurement(
                    value=p75_ms, unit="ms", evidence="B",
                    aggregation="p75", sample_count=len(latencies)),
                "provision_ready_ms_stdev": Measurement(
                    value=stdev_ms, unit="ms", evidence="B",
                    sample_count=len(latencies)),
                "provision_samples": Measurement(
                    value=len(latencies), unit="count", evidence="B"),
                "provision_ready": Measurement(value=ready, unit="", evidence="B"),
            },
            findings=findings,
            notes=(f"provision create->ready p25={p25_ms}ms p50={median_ms}ms "
                   f"p75={p75_ms}ms stdev={stdev_ms}ms (n={len(latencies)}, ready={ready})"),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
