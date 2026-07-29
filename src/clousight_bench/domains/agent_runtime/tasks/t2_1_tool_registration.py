"""T2.1 tool registration paths.

Which registration paths does the runtime accept: MCP, OpenAPI, or a native
connector? We attempt to register the same trivial tool via each path and
record which the runtime accepts. More accepted paths = more integration
surface. CapabilityNotSupported on a path = that path is unavailable.

Evidence layer B: this observes a platform capability; the exact set is
environment-dependent (region / plan), but the probe method is reproducible.
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
from clousight_bench.domains.agent_runtime.adapters.base import (
    AgentRuntimeAdapter,
    CapabilityNotSupported,
)

_PATHS = ("mcp", "openapi", "native")
_TOOL_SPEC = {"name": "echo", "description": "return input unchanged"}


class ToolRegistrationTask(Task):
    task_id = "T2.1"
    title = "Tool registration paths"
    evidence_layer = "B"
    required_permissions = (perm.TOOL_REGISTER,)
    task_revision = "2"
    scorer_revision = "2"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "paths": list(_PATHS), "tool_spec": _TOOL_SPEC}

    def environment_facts(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> dict[str, Any]:
        return {"probed_paths": list(_PATHS)}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T2.1 needs an AgentRuntimeAdapter")
        session = adapter.create_session()
        support: dict[str, bool] = {}
        try:
            for path in _PATHS:
                try:
                    support[path] = bool(adapter.register_tool(path, _TOOL_SPEC))
                except CapabilityNotSupported:
                    support[path] = False
        finally:
            adapter.destroy_session(session)
        return ObservationBundle(
            observations={"support": support, "tool_spec": dict(_TOOL_SPEC)}
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        support = dict(observations.observations.get("support", {}))
        supported = sorted(path for path, ok in support.items() if ok)
        findings: list[Finding] = []
        if not supported:
            findings.append(
                Finding(
                    code="agent_runtime.no_tool_registration_path",
                    severity="warning",
                    summary="runtime accepts none of the MCP, OpenAPI or native paths",
                    evidence="B",
                    details={"support": support},
                )
            )
        return TaskResult(
            measurements={
                "supported_paths": Measurement(value=supported, unit="", evidence="B"),
                "supported_count": Measurement(
                    value=len(supported), unit="count", evidence="B"
                ),
                "mcp": Measurement(value=bool(support.get("mcp")), unit="", evidence="B"),
                "openapi": Measurement(
                    value=bool(support.get("openapi")), unit="", evidence="B"
                ),
                "native": Measurement(
                    value=bool(support.get("native")), unit="", evidence="B"
                ),
            },
            findings=findings,
            notes=f"registration paths supported: {', '.join(supported) or 'none'}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
            unsupported=not supported,
        )
