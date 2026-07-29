"""T1.3 tool-failure recovery.

Deterministic, replayable, evidence layer C:
  - pin the tool universe (mock server), inject a fault on the 3rd tool call
  - run the agent's tool plan under the platform's runtime semantics (via adapter)
  - classify recovery behavior from the observed attempts

An auto-retry recovery means the runtime absorbed the transient fault; a
fail-fast abort means it surfaced the fault to the caller. Both are findings.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any
from urllib import request

from clousight_bench.core.observation import (
    Finding,
    Measurement,
    ObservationBundle,
    TaskResult,
)
from clousight_bench.core.plugin import ProviderAdapter, Task
from clousight_bench.domains.agent_runtime import permissions as perm
from clousight_bench.domains.agent_runtime.adapters.base import AgentRuntimeAdapter, ToolCall

# The agent plan: read prices 5 times (a long-ish tool loop). Fault hits call #3.
PLAN = [ToolCall(target="prices", params={"provider": "aws"}) for _ in range(5)]

# Deterministic fault: only the 3rd call to /prices returns 500 (transient outage).
FAULT = {"target": "prices", "fail_on_calls": [3], "status": 500}


def _post(base_url: str, path: str, body: dict[str, Any]) -> None:
    data = json.dumps(body).encode("utf-8")
    req = request.Request(f"{base_url}{path}", data=data, method="POST",
                          headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=10) as resp:
        resp.read()


class FaultRecoveryTask(Task):
    task_id = "T1.3"
    title = "Tool-failure recovery"
    evidence_layer = "C"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    task_revision = "2"
    scorer_revision = "2"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "plan": [{"target": c.target, "method": c.method, "params": c.params} for c in PLAN],
            "fault": FAULT,
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
        mock = adapter.mock_base_url.rstrip("/")

        # 1. reset + arm the deterministic fault
        _post(mock, "/reset", {})
        _post(mock, "/fault/config", FAULT)

        # 2. run the plan under the runtime's own recovery semantics
        session = adapter.create_session()
        try:
            trace = adapter.run_tool_plan(session, PLAN)
        finally:
            adapter.destroy_session(session)

        return ObservationBundle(
            observations={
                "fault": dict(FAULT),
                "plan_calls": len(PLAN),
                "completed": trace.completed,
                "final_state": trace.final_state,
                "attempts": [asdict(a) for a in trace.attempts],
            }
        )

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
        elif not completed and final_state == "aborted":
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
