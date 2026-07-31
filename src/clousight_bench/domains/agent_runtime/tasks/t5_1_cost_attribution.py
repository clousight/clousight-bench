"""T5.1 cost attribution.

Run a fixed reference workload and record what it *consumed* in the standard
usage vocabulary (``core/usage.py``): invocations and vcpu_hours. Usage is the
task's job; turning it into money is the pricing layer's job.

``execute`` drives the plan and captures raw usage; ``score`` emits the usage as
measurements named exactly ``invocations`` / ``vcpu_hours`` so the reference
pricing enricher (or a commercial feed) can price them post-run. For a
self-contained run you may pass an inline price
(``target.pricing = {per_invocation_usd, per_vcpu_hour_usd}``); ``score`` then
also emits ``cost_usd``. Prices are a documentation input (evidence A); the
usage is measured (evidence B), which is the load-bearing part.
"""
from __future__ import annotations

import json
import time
from typing import Any
from urllib import request

from clousight_bench.core.observation import Measurement, ObservationBundle, TaskResult
from clousight_bench.core.plugin import ProviderAdapter, Task
from clousight_bench.core.usage import USAGE_METRIC_KEYS
from clousight_bench.domains.agent_runtime import permissions as perm
from clousight_bench.domains.agent_runtime.adapters.base import AgentRuntimeAdapter, ToolCall

# A fixed, fault-free reference plan: read prices 8 times.
PLAN = [ToolCall(target="prices", params={"provider": "aws"}) for _ in range(8)]


def _post(base_url: str, path: str, body: dict[str, Any]) -> None:
    data = json.dumps(body).encode("utf-8")
    req = request.Request(f"{base_url}{path}", data=data, method="POST",
                          headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=10) as resp:
        resp.read()


class CostAttributionTask(Task):
    task_id = "T5.1"
    title = "Cost attribution"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "2"  # dropped the inline cost_usd measurement (usage-only)
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id,
                "plan": [{"target": c.target, "params": c.params} for c in PLAN]}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T5.1 needs an AgentRuntimeAdapter")
        mock = adapter.mock_base_url.rstrip("/")
        _post(mock, "/reset", {})  # clear any fault/latency armed by a prior task
        vcpus = float((adapter.target or {}).get("vcpus", 1))
        session = adapter.create_session()
        start = time.perf_counter()
        try:
            trace = adapter.run_tool_plan(session, PLAN)
        finally:
            adapter.destroy_session(session)
        duration_s = time.perf_counter() - start
        return ObservationBundle(
            observations={
                "invocations": len(trace.attempts),
                "vcpu_hours": round(duration_s / 3600 * vcpus, 8),
                "duration_ms": round(duration_s * 1000, 2),
                "completed": trace.completed,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        invocations = int(raw.get("invocations", 0))
        vcpu_hours = float(raw.get("vcpu_hours", 0.0))
        measurements = {
            "invocations": Measurement(value=invocations, unit="", evidence="B"),
            "vcpu_hours": Measurement(value=vcpu_hours, unit="vcpu_hours", evidence="B"),
            "duration_ms": Measurement(
                value=raw.get("duration_ms", 0.0), unit="ms", evidence="B"),
        }
        # Cost is the pricing enricher's job (single cost authority): this task
        # reports usage only, in the shared USAGE_METRIC_KEYS vocabulary.
        assert set(measurements) & set(USAGE_METRIC_KEYS)  # usage is present for pricing
        return TaskResult(
            measurements=measurements,
            notes=f"{invocations} invocations, {vcpu_hours} vcpu_hours; "
                  f"usage only; cost from the pricing enricher",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
