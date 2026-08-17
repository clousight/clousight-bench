"""T1.10 retry storm.

Evidence layer B — mock-counted total attempts + storm-bounded-by attribution:
  - configure the mock server to fail ALL calls on a per-correlation bucket
    (POST /fault/config {fail_from_call:1, fail_count:999, corr:<uuid>})
  - issue a single invoke with that correlation id
  - the deployed agent retries internally per its lc_agent 5xx-retry-2 contract
    (up to 3 total attempts: 1 original + 2 retries)
  - read the mock server's call counter (GET /fault/state) to observe how many
    times the platform actually let the agent hit the tool

Storm-bounded-by attribution:
  total_attempts <= 3 and no timeout → "agent"   (agent contract bounded the storm)
  invoke raised Timeout               → "platform" (platform cut it before exhaustion)
  total_attempts > 3                  → "none"    (anomaly — unbounded retry storm risk)
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

# Observation window: the runtime must decide within this many seconds.
MAX_WINDOW_S = 30.0


class RetryStormTask(Task):
    task_id = "T1.10"
    title = "Retry storm"
    evidence_layer = "B"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("reliability/retry-storm",)
    task_revision = "2"
    scorer_revision = "2"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "fault": "all calls fail (mock corr-bucket fail_from_call:1 fail_count:999)",
            "max_window_s": MAX_WINDOW_S,
            "injection_method": "mock-server corr-bucket (platform-visible)",
        }

    def environment_facts(self, adapter: ProviderAdapter, params: dict[str, Any]) -> dict[str, Any]:
        recovery = adapter.target.get("recovery", {})
        return {
            "recovery_policy": str(recovery.get("mode", "auto-retry")),
            "max_retries": int(recovery.get("max_retries", 3)),
        }

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.10 needs an AgentRuntimeAdapter")
        return adapter.run_data_plane_probe("retry_storm", {"max_window_s": MAX_WINDOW_S})

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        retry_storm_capability = str(raw.get("capability", "supported"))
        total_attempts: int = int(raw.get("total_attempts", 0))
        storm_bounded_by: str = str(raw.get("storm_bounded_by", "agent"))
        duration_ms: float = float(raw.get("duration_ms", 0.0))
        # duration_ms is now the warm-path storm window; the ~86s cold start is
        # absorbed by the probe's ensure_warm and reported separately (None on
        # local-sim, which has no cold-start phase).
        cold_start_ms = raw.get("cold_start_ms")

        findings: list[Finding] = []

        if storm_bounded_by == "none":
            findings.append(
                Finding(
                    code="agent_runtime.retry_storm_unbounded",
                    severity="critical",
                    summary="unbounded retry storm risk — total_attempts exceeded agent contract",
                    evidence="B",
                    details={
                        "total_attempts": total_attempts,
                        "storm_bounded_by": storm_bounded_by,
                        "duration_ms": duration_ms,
                        "max_window_s": MAX_WINDOW_S,
                    },
                )
            )
        elif storm_bounded_by == "platform":
            findings.append(
                Finding(
                    code="agent_runtime.retry_storm_platform_bounded",
                    severity="info",
                    summary="platform bounded storm via invoke timeout",
                    evidence="B",
                    details={
                        "total_attempts": total_attempts,
                        "storm_bounded_by": storm_bounded_by,
                        "duration_ms": duration_ms,
                    },
                )
            )

        return TaskResult(
            measurements={
                "retry_storm_capability": Measurement(value=retry_storm_capability, unit="", evidence="B"),
                "total_attempts": Measurement(value=total_attempts, unit="count", evidence="B"),
                "storm_bounded_by": Measurement(value=storm_bounded_by, unit="", evidence="B"),
                "duration_ms": Measurement(value=duration_ms, unit="ms", evidence="B"),
                "cold_start_ms": Measurement(value=cold_start_ms, unit="ms", evidence="B"),
            },
            findings=findings,
            notes=(
                f"all-fail probe → storm_bounded_by={storm_bounded_by}, "
                f"total_attempts={total_attempts} in {duration_ms:.0f}ms"
            ),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
