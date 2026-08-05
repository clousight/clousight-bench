"""T1.10 retry storm.

When ALL tool calls fail persistently, does the runtime abort cleanly
(fail-fast) or loop indefinitely, amplifying failures into a retry storm?

Evidence layer C: deterministic, replayable.
  - Run a 5-call plan with every call guaranteed to fail (non-existent path).
  - Observe whether the runtime aborts on the first failure or keeps retrying
    until the 30-second window expires.
  - Classify:
      "abort_on_first_failure" -- no retry amplification, good
      "timeout_loop"           -- runtime looped until the window expired (bad)
      "unexpected_success"     -- calls unexpectedly succeeded (probe invalid)
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
from clousight_bench.domains.agent_runtime.adapters.base import AgentRuntimeAdapter, CapabilityNotSupported

# Observation window: the runtime must decide within this many seconds.
MAX_WINDOW_S = 30.0


class RetryStormTask(Task):
    task_id = "T1.10"
    title = "Retry storm"
    evidence_layer = "C"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("reliability/retry-storm",)
    task_revision = "1"
    scorer_revision = "1"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "plan_calls": 5,
            "fault": "all calls fail (non-existent endpoint)",
            "max_window_s": MAX_WINDOW_S,
        }

    def environment_facts(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> dict[str, Any]:
        recovery = adapter.target.get("recovery", {})
        return {
            "recovery_policy": str(recovery.get("mode", "auto-retry")),
            "max_retries": int(recovery.get("max_retries", 3)),
        }

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.10 needs an AgentRuntimeAdapter")

        result = adapter.probe_retry_storm(max_window_s=MAX_WINDOW_S)

        return ObservationBundle(
            observations={
                "storm_behavior": result.storm_behavior,
                "calls_attempted": result.calls_attempted,
                "duration_ms": result.duration_ms,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        storm_behavior = str(raw.get("storm_behavior", ""))
        calls_attempted = int(raw.get("calls_attempted", 0))
        duration_ms = float(raw.get("duration_ms", 0.0))

        findings: list[Finding] = []
        if storm_behavior == "timeout_loop":
            findings.append(
                Finding(
                    code="agent_runtime.retry_storm_risk",
                    severity="warning",
                    summary="runtime looped on persistent tool failures until the window expired",
                    evidence="C",
                    details={
                        "calls_attempted": calls_attempted,
                        "duration_ms": duration_ms,
                        "max_window_s": MAX_WINDOW_S,
                    },
                )
            )

        return TaskResult(
            measurements={
                "storm_behavior": Measurement(
                    value=storm_behavior, unit="", evidence="C"
                ),
                "calls_attempted": Measurement(
                    value=calls_attempted, unit="count", evidence="C"
                ),
                "probe_duration_ms": Measurement(
                    value=duration_ms, unit="ms", evidence="C"
                ),
            },
            findings=findings,
            notes=f"all-fail plan -> storm_behavior={storm_behavior}, {calls_attempted} attempts in {duration_ms:.0f}ms",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
