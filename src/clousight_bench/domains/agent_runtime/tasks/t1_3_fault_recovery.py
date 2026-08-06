"""T1.3 tool-failure recovery.

Deterministic, replayable, evidence layer C:
  - encode a fault spec in the request body (fail_after_n_calls=3) so the
    deployed agent returns a synthetic 500 on the 3rd call, without relying on
    shared mock-server state (which breaks when the FC function has multiple
    instances — a different instance receives the fault-arm POST vs. the invoke)
  - run via adapter.probe_fault_recovery() which delegates to the transport
  - classify recovery behavior from the observed attempts

An auto-retry recovery means the runtime absorbed the transient fault; a
fail-fast abort means it surfaced the fault to the caller. Both are findings.
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

# Fault fires on the 3rd tool call (1-indexed).
FAULT_CALL_INDEX = 3

# Deterministic fault description (for config / scorer context only).
FAULT = {"target": "prices", "fail_on_calls": [FAULT_CALL_INDEX], "status": 500}


class FaultRecoveryTask(Task):
    task_id = "T1.3"
    title = "Tool-failure recovery"
    evidence_layer = "C"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("reliability/fault-recovery",)
    task_revision = "3"
    scorer_revision = "2"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "fault_call_index": FAULT_CALL_INDEX,
            "fault": FAULT,
            "injection_method": "request-level (fail_after_n_calls)",
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
            raise TypeError("T1.3 needs an AgentRuntimeAdapter")
        return adapter.run_data_plane_probe("fault_recovery", {"fault_call_index": FAULT_CALL_INDEX})

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        attempts = list(raw.get("attempts", []))
        failures = [a for a in attempts if not a["ok"]]
        retried = any(a["attempt"] > 1 for a in attempts)
        completed = bool(raw.get("completed"))
        final_state = str(raw.get("final_state", ""))

        if not failures:
            recovery_mode = "no-fault-observed"  # fault never triggered -> test invalid
        elif completed and retried:
            recovery_mode = "auto-retry"
        elif not completed:
            # Any non-completion after a fault is fail-fast: the runtime surfaced the
            # error rather than absorbing it. The exact final_state label ("aborted",
            # "failed") is platform-specific and must not gate the classification.
            recovery_mode = "fail-fast"
        else:
            recovery_mode = "manual-resume"

        # Latency spent on failed attempts before the run either recovered or gave up.
        ttr_ms = round(sum(a["latency_ms"] for a in failures), 2)

        findings: list[Finding] = []
        if recovery_mode == "no-fault-observed":
            findings.append(
                Finding(
                    code="agent_runtime.fault_not_observed",
                    severity="critical",
                    summary="the injected fault never fired, so this run measures nothing",
                    evidence="C",
                    details={
                        "fault": raw.get("fault", {}),
                        "attempts": len(attempts),
                    },
                )
            )
        elif recovery_mode == "fail-fast":
            findings.append(
                Finding(
                    code="agent_runtime.recovery_fail_fast",
                    severity="warning",
                    summary="runtime aborted on the first tool fault instead of retrying",
                    evidence="C",
                    details={"final_state": final_state},
                )
            )

        return TaskResult(
            measurements={
                "recovery_mode": Measurement(
                    value=recovery_mode, unit="", evidence="C"
                ),
                "final_state": Measurement(
                    value=final_state, unit="", evidence="C"
                ),
                "budgeted_success": Measurement(
                    value=completed, unit="", evidence="C"
                ),
                "time_to_recovery_ms": Measurement(
                    value=ttr_ms,
                    unit="ms",
                    evidence="C",
                    aggregation="sum",
                    sample_count=len(failures),
                ),
                "total_attempts": Measurement(
                    value=len(attempts), unit="count", evidence="C"
                ),
                "fault_hits": Measurement(
                    value=len(failures), unit="count", evidence="C"
                ),
                "retried": Measurement(value=retried, unit="", evidence="C"),
            },
            findings=findings,
            notes=f"fault on call #{FAULT['fail_on_calls']}; runtime recovery_mode={recovery_mode}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
