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
from typing import Any
from urllib import request

from clousight_bench.core.plugin import ProviderAdapter, Task, TaskOutput
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

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "plan": [{"target": c.target, "method": c.method, "params": c.params} for c in PLAN],
            "fault": FAULT,
        }

    def run(self, adapter: ProviderAdapter, params: dict[str, Any]) -> TaskOutput:
        assert isinstance(adapter, AgentRuntimeAdapter), "T1.3 needs an AgentRuntimeAdapter"
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

        # 3. classify recovery from the observed attempts
        attempts = trace.attempts
        failures = [a for a in attempts if not a.ok]
        retried = any(a.attempt > 1 for a in attempts)
        recovered = trace.completed and bool(failures)  # hit a fault AND still finished

        if not failures:
            recovery_mode = "no-fault-observed"  # fault never triggered -> test invalid
        elif recovered and retried:
            recovery_mode = "auto-retry"
        elif not trace.completed and trace.final_state == "aborted":
            recovery_mode = "fail-fast"
        else:
            recovery_mode = "manual-resume"

        # time_to_recovery = latency spent on failed attempts before the first success after a fault
        ttr_ms = round(sum(a.latency_ms for a in attempts if not a.ok), 2)

        metrics = {
            "recovery_mode": recovery_mode,
            "final_state": trace.final_state,
            "budgeted_success": trace.completed,  # completed within the retry budget
            "time_to_recovery_ms": ttr_ms,
            "total_attempts": len(attempts),
            "fault_hits": len(failures),
            "retried": retried,
        }
        return TaskOutput(
            metrics=metrics,
            evidence_layer=self.evidence_layer,
            ok=recovery_mode != "no-fault-observed",
            raw={"attempts": [a.__dict__ for a in attempts]},
            notes=f"fault on call #{FAULT['fail_on_calls']}; runtime recovery_mode={recovery_mode}",
        )
