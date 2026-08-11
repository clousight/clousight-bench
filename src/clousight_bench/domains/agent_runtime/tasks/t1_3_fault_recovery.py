"""T1.3 tool-failure recovery.

Evidence layer B — real platform-visible fault injection:
  - configure the mock server to fail call #1 on a per-correlation bucket
    (POST /fault/config {target:"prices", fail_on_calls:[1], status:500, corr:<uuid>})
  - issue a single invoke with that correlation id
  - the deployed agent retries internally per its lc_agent 5xx-retry-2 contract
    (3 total attempts: 1 original + 2 retries)
  - read the mock server's call counter (GET /fault/state) to observe how many
    times the platform actually let the agent hit the tool

Three-state platform attribution:
  recovered=True,  observed_attempts=3 → platform let agent retry until success
  recovered=False, observed_attempts=3 → platform let agent retry, tool stayed broken
  platform_terminated=True             → platform killed the invoke during recovery
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


class FaultRecoveryTask(Task):
    task_id = "T1.3"
    title = "Tool-failure recovery"
    evidence_layer = "B"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("reliability/fault-recovery",)
    task_revision = "4"
    scorer_revision = "3"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "fault": {"target": "prices", "fail_on_calls": [1], "status": 500},
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
            raise TypeError("T1.3 needs an AgentRuntimeAdapter")
        return adapter.run_data_plane_probe("fault_recovery", {})

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        recovered: bool = bool(raw.get("recovered", False))
        observed_attempts: int = int(raw.get("observed_attempts", 0))
        recovery_ms: float = float(raw.get("recovery_ms", 0.0))
        platform_terminated: bool = bool(raw.get("platform_terminated", False))

        findings: list[Finding] = []

        if platform_terminated:
            findings.append(
                Finding(
                    code="agent_runtime.platform_timeout_recovery",
                    severity="warning",
                    summary=(
                        "平台 timeout 在 agent 恢复窗口内杀掉 invoke"
                        " (platform terminated invoke during recovery)"
                    ),
                    evidence="B",
                    details={"recovery_ms": recovery_ms},
                )
            )
        elif not recovered and observed_attempts <= 1:
            findings.append(
                Finding(
                    code="agent_runtime.platform_blocked_retry",
                    severity="warning",
                    summary="platform did not let the agent retry: observed_attempts<=1 and not recovered",
                    evidence="B",
                    details={"observed_attempts": observed_attempts},
                )
            )

        # recovery_capability: "supported" when the mock saw ≥ 1 attempt
        recovery_capability = "supported" if observed_attempts > 0 else "unknown"

        return TaskResult(
            measurements={
                "recovery_capability": Measurement(value=recovery_capability, unit="", evidence="B"),
                "recovered": Measurement(value=recovered, unit="", evidence="B"),
                "observed_attempts": Measurement(value=observed_attempts, unit="count", evidence="B"),
                "recovery_ms": Measurement(value=round(recovery_ms, 2), unit="ms", evidence="B"),
                "platform_terminated": Measurement(value=platform_terminated, unit="", evidence="B"),
            },
            findings=findings,
            notes=(
                f"fault on prices call #1 (corr-bucket); "
                f"recovered={recovered}, observed_attempts={observed_attempts}"
            ),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
