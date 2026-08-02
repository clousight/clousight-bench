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

from clousight_bench.core.observation import (
    Finding,
    Measurement,
    ObservationBundle,
    TaskResult,
)
from clousight_bench.core.plugin import ProviderAdapter, Task
from clousight_bench.domains.agent_runtime import openinference
from clousight_bench.domains.agent_runtime import permissions as perm
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
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE, perm.TRACE_EXPORT)
    capability_tags = ("observability/tracing",)
    task_revision = "2"
    scorer_revision = "2"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "plan": [{"target": c.target, "params": c.params} for c in PLAN],
        }

    def environment_facts(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> dict[str, Any]:
        trace = adapter.target.get("trace", {})
        return {"otel_export_policy": bool(trace.get("otel_export", True))}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T4.2 needs an AgentRuntimeAdapter")
        _post(adapter.mock_base_url.rstrip("/"), "/reset", {})
        session = adapter.create_session()
        try:
            adapter.run_tool_plan(session, PLAN)
            try:
                payload = adapter.export_otel(session)
            except CapabilityNotSupported as exc:
                return ObservationBundle(
                    observations={"capability": "unsupported", "reason": str(exc)}
                )
        finally:
            adapter.destroy_session(session)
        return ObservationBundle(
            observations={"capability": "supported", "otel": payload}
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "otel_export_supported": Measurement(
                        value=False, unit="", evidence="B"
                    ),
                    "otel_valid": Measurement(value=False, unit="", evidence="B"),
                },
                findings=[
                    Finding(
                        code="agent_runtime.otel_export_absent",
                        severity="info",
                        summary="runtime cannot export OTel",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime cannot export OTel",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        payload = raw.get("otel", {})
        valid, problems = openinference.validate_otel(payload)
        span_count = sum(
            len(ss.get("spans", []))
            for rs in payload.get("resourceSpans", [])
            for ss in rs.get("scopeSpans", [])
        )
        findings: list[Finding] = []
        if not valid:
            findings.append(
                Finding(
                    code="agent_runtime.otel_payload_invalid",
                    severity="warning",
                    summary="exported OTel payload does not match the minimal OTLP shape",
                    evidence="B",
                    details={"problems": problems},
                )
            )
        return TaskResult(
            measurements={
                "otel_export_supported": Measurement(
                    value=True, unit="", evidence="B"
                ),
                "otel_valid": Measurement(value=valid, unit="", evidence="B"),
                "span_count": Measurement(value=span_count, unit="count", evidence="B"),
                "problems": Measurement(value=problems, unit="", evidence="B"),
            },
            findings=findings,
            notes=f"OTel export valid={valid}; spans={span_count}; problems={problems or 'none'}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
