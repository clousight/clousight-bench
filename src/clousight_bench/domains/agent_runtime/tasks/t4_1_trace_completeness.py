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

PLAN = [ToolCall(target="prices", params={"provider": "aws"}) for _ in range(3)]


def _post(base_url: str, path: str, body: dict[str, Any], token: str | None = None) -> None:
    data = json.dumps(body).encode("utf-8")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["X-Clousight-Token"] = token
    req = request.Request(f"{base_url}{path}", data=data, method="POST", headers=headers)
    with request.urlopen(req, timeout=10) as resp:
        resp.read()


class TraceCompletenessTask(Task):
    task_id = "T4.1"
    title = "Trace span completeness (OpenInference)"
    evidence_layer = "C"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE, perm.TRACE_READ)
    capability_tags = ("observability/tracing",)
    task_revision = "2"
    scorer_revision = "2"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "plan": [{"target": c.target, "params": c.params} for c in PLAN],
            "expected_kinds": list(openinference.SPAN_KINDS),
        }

    def environment_facts(self, adapter: ProviderAdapter, params: dict[str, Any]) -> dict[str, Any]:
        trace = adapter.target.get("trace", {})
        return {"trace_completeness_policy": str(trace.get("completeness", "full"))}

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T4.1 needs an AgentRuntimeAdapter")
        _token: str | None = (adapter.target or {}).get("mock_token") or None
        _post(adapter.mock_base_url.rstrip("/"), "/reset", {}, _token)
        session = adapter.create_session()
        try:
            adapter.run_tool_plan(session, PLAN)
            try:
                spans = adapter.get_trace(session)
            except CapabilityNotSupported as exc:
                return ObservationBundle(
                    observations={
                        "capability": "unsupported",
                        "tool_calls": len(PLAN),
                        "reason": str(exc),
                    }
                )
        finally:
            adapter.destroy_session(session)
        return ObservationBundle(
            observations={
                "capability": "supported",
                "tool_calls": len(PLAN),
                "spans": spans,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "trace_capability": Measurement(value="unsupported", unit="", evidence="C"),
                    "span_completeness": Measurement(value=0.0, unit="ratio", evidence="C"),
                },
                findings=[
                    Finding(
                        code="agent_runtime.trace_api_absent",
                        severity="info",
                        summary="runtime exposes no trace API",
                        evidence="C",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no trace API",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        spans = list(raw.get("spans", []))
        tool_calls = int(raw.get("tool_calls", 0))
        completeness = openinference.span_completeness(spans, tool_calls)
        present = openinference.kinds_present(spans)
        missing = sorted(set(openinference.SPAN_KINDS) - present)
        findings: list[Finding] = []
        if completeness < 1.0:
            findings.append(
                Finding(
                    code="agent_runtime.trace_spans_missing",
                    severity="warning",
                    summary="runtime trace is missing spans the OpenInference shape requires",
                    evidence="C",
                    details={"kinds_missing": missing, "completeness": completeness},
                )
            )
        return TaskResult(
            measurements={
                "trace_capability": Measurement(value="supported", unit="", evidence="C"),
                "span_completeness": Measurement(value=completeness, unit="ratio", evidence="C"),
                "spans_present": Measurement(value=len(spans), unit="count", evidence="C"),
                "spans_expected": Measurement(
                    value=openinference.expected_span_count(tool_calls),
                    unit="count",
                    evidence="C",
                ),
                "kinds_present": Measurement(value=sorted(present), unit="", evidence="C"),
                "kinds_missing": Measurement(value=missing, unit="", evidence="C"),
            },
            findings=findings,
            notes=f"span completeness {completeness:.0%}; missing kinds {missing or 'none'}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
