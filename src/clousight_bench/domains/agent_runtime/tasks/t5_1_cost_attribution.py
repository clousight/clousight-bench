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


def _post(base_url: str, path: str, body: dict[str, Any], token: str | None = None) -> None:
    data = json.dumps(body).encode("utf-8")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["X-Clousight-Token"] = token
    req = request.Request(f"{base_url}{path}", data=data, method="POST", headers=headers)
    with request.urlopen(req, timeout=10) as resp:
        resp.read()


class CostAttributionTask(Task):
    task_id = "T5.1"
    title = "Cost attribution"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "2"  # dropped the inline cost_usd measurement (usage-only)
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("cost/attribution",)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "plan": [{"target": c.target, "params": c.params} for c in PLAN]}

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T5.1 needs an AgentRuntimeAdapter")
        mock = adapter.mock_base_url.rstrip("/")
        token: str | None = (adapter.target or {}).get("mock_token") or None
        _post(mock, "/reset", {}, token)
        vcpus = float((adapter.target or {}).get("vcpus", 1))
        session = adapter.create_session()
        start = time.perf_counter()
        try:
            trace = adapter.run_tool_plan(session, PLAN)
        finally:
            adapter.destroy_session(session)
        duration_s = time.perf_counter() - start
        successful_calls = sum(1 for a in trace.attempts if a.ok)
        # Inline cost estimate: if target provides per_invocation_usd / per_vcpu_hour_usd,
        # compute cost_usd here so score() can derive per-call cost.
        vcpu_hours_val = round(duration_s / 3600 * vcpus, 8)
        pricing = (adapter.target or {}).get("pricing") or {}
        cost_usd: float | None = None
        if pricing:
            per_inv = float(pricing.get("per_invocation_usd") or 0)
            per_vcpu = float(pricing.get("per_vcpu_hour_usd") or 0)
            cost_usd = round(len(trace.attempts) * per_inv + vcpu_hours_val * per_vcpu, 9)
        obs: dict = {
            "invocations": len(trace.attempts),
            "vcpu_hours": vcpu_hours_val,
            "duration_ms": round(duration_s * 1000, 2),
            "completed": trace.completed,
            "successful_calls": successful_calls,
        }
        if cost_usd is not None:
            obs["cost_usd"] = cost_usd
        return ObservationBundle(observations=obs)

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        invocations = int(raw.get("invocations", 0))
        vcpu_hours = float(raw.get("vcpu_hours", 0.0))
        successful_calls = int(raw.get("successful_calls", invocations))
        measurements = {
            "invocations": Measurement(value=invocations, unit="", evidence="B"),
            "vcpu_hours": Measurement(value=vcpu_hours, unit="vcpu_hours", evidence="B"),
            "duration_ms": Measurement(value=raw.get("duration_ms", 0.0), unit="ms", evidence="B"),
        }
        # Cost is the pricing enricher's job (single cost authority): this task
        # reports usage only, in the shared USAGE_METRIC_KEYS vocabulary.
        assert set(measurements) & set(USAGE_METRIC_KEYS)  # usage is present for pricing
        # If an inline cost_usd was computed in execute() (from target.pricing),
        # also emit cost_per_successful_call_usd for a normalized cost view.
        notes = (
            f"{invocations} invocations, {vcpu_hours} vcpu_hours; usage only; cost from the pricing enricher"
        )
        cost_usd = raw.get("cost_usd")
        if cost_usd is not None and successful_calls > 0:
            cost_per_call = round(cost_usd / successful_calls, 9)
            measurements["cost_per_successful_call_usd"] = Measurement(
                value=cost_per_call,
                unit="USD",
                evidence="A",
            )
            notes += f"; cost_per_call={cost_per_call:.6f} USD (inline pricing)"
        return TaskResult(
            measurements=measurements,
            notes=notes,
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
