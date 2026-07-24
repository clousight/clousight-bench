"""T4.2 OTel export compatibility.

Run a short tool plan, then ask the runtime to export its trace as OTLP and
validate it against the minimal OTLP shape (resourceSpans -> scopeSpans ->
spans, each span with spanId + name, resource with service.name).
CapabilityNotSupported = the runtime cannot export OTel at all.

Evidence layer B: capability + format conformance; whether a platform exports
OTel is environment-dependent, but the validation is reproducible.
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

PLAN = [ToolCall(target="prices", params={"provider": "aws"}) for _ in range(2)]


def _post(base_url: str, path: str, body: dict[str, Any]) -> None:
    data = json.dumps(body).encode("utf-8")
    req = request.Request(f"{base_url}{path}", data=data, method="POST",
                          headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=10) as resp:
        resp.read()


class OtelExportTask(Task):
    task_id = "T4.2"
    title = "OTel export compatibility"
    evidence_layer = "B"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "plan": [{"target": c.target, "params": c.params} for c in PLAN],
        }

    def run(self, adapter: ProviderAdapter, params: dict[str, Any]) -> TaskOutput:
        assert isinstance(adapter, AgentRuntimeAdapter), "T4.2 needs an AgentRuntimeAdapter"
        _post(adapter.mock_base_url.rstrip("/"), "/reset", {})
        session = adapter.create_session()
        try:
            adapter.run_tool_plan(session, PLAN)
            try:
                payload = adapter.export_otel(session)
            except CapabilityNotSupported as exc:
                return TaskOutput(
                    metrics={"otel_export_supported": False, "otel_valid": False},
                    evidence_layer=self.evidence_layer,
                    ok=True,
                    notes=f"runtime cannot export OTel: {exc}",
                )
        finally:
            adapter.destroy_session(session)

        valid, problems = openinference.validate_otel(payload)
        span_count = sum(
            len(ss.get("spans", []))
            for rs in payload.get("resourceSpans", [])
            for ss in rs.get("scopeSpans", [])
        )
        metrics = {
            "otel_export_supported": True,
            "otel_valid": valid,
            "span_count": span_count,
            "problems": problems,
        }
        return TaskOutput(
            metrics=metrics,
            evidence_layer=self.evidence_layer,
            ok=True,
            raw={"otel": payload},
            notes=f"OTel export valid={valid}; spans={span_count}; problems={problems or 'none'}",
        )
