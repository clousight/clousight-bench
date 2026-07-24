"""T4.1 trace span completeness (OpenInference).

Run a short, fault-free tool plan, then ask the runtime for its own trace and
score it against the OpenInference reference shape: one root CHAIN span, one LLM
span, and one TOOL span per tool call. A runtime that drops spans (e.g. no tool
spans) scores below 1.0. CapabilityNotSupported = no trace API.

Evidence layer C: deterministic against the pinned tool universe on local-sim.
"""
from __future__ import annotations

import json
from typing import Any
from urllib import request

from clousight_bench.core.plugin import ProviderAdapter, Task, TaskOutput
from clousight_bench.domains.agent_runtime import openinference
from clousight_bench.domains.agent_runtime.adapters.base import (
    AgentRuntimeAdapter,
    CapabilityNotSupported,
    ToolCall,
)

PLAN = [ToolCall(target="prices", params={"provider": "aws"}) for _ in range(3)]


def _post(base_url: str, path: str, body: dict[str, Any]) -> None:
    data = json.dumps(body).encode("utf-8")
    req = request.Request(f"{base_url}{path}", data=data, method="POST",
                          headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=10) as resp:
        resp.read()


class TraceCompletenessTask(Task):
    task_id = "T4.1"
    title = "Trace span completeness (OpenInference)"
    evidence_layer = "C"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "plan": [{"target": c.target, "params": c.params} for c in PLAN],
            "expected_kinds": list(openinference.SPAN_KINDS),
        }

    def run(self, adapter: ProviderAdapter, params: dict[str, Any]) -> TaskOutput:
        assert isinstance(adapter, AgentRuntimeAdapter), "T4.1 needs an AgentRuntimeAdapter"
        _post(adapter.mock_base_url.rstrip("/"), "/reset", {})
        session = adapter.create_session()
        try:
            adapter.run_tool_plan(session, PLAN)
            try:
                spans = adapter.get_trace(session)
            except CapabilityNotSupported as exc:
                return TaskOutput(
                    metrics={"trace_capability": "unsupported", "span_completeness": 0.0},
                    evidence_layer=self.evidence_layer,
                    ok=True,
                    notes=f"runtime exposes no trace API: {exc}",
                )
        finally:
            adapter.destroy_session(session)

        tool_calls = len(PLAN)
        completeness = openinference.span_completeness(spans, tool_calls)
        present = openinference.kinds_present(spans)
        metrics = {
            "trace_capability": "supported",
            "span_completeness": completeness,
            "spans_present": len(spans),
            "spans_expected": openinference.expected_span_count(tool_calls),
            "kinds_present": sorted(present),
            "kinds_missing": sorted(set(openinference.SPAN_KINDS) - present),
        }
        return TaskOutput(
            metrics=metrics,
            evidence_layer=self.evidence_layer,
            ok=True,
            raw={"spans": spans},
            notes=f"span completeness {completeness:.0%}; missing kinds {metrics['kinds_missing'] or 'none'}",
        )
