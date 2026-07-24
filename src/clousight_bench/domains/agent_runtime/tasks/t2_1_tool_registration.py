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

from clousight_bench.core.plugin import ProviderAdapter, Task, TaskOutput
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

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "paths": list(_PATHS), "tool_spec": _TOOL_SPEC}

    def run(self, adapter: ProviderAdapter, params: dict[str, Any]) -> TaskOutput:
        assert isinstance(adapter, AgentRuntimeAdapter), "T2.1 needs an AgentRuntimeAdapter"
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

        supported = sorted(p for p, ok in support.items() if ok)
        metrics = {
            "supported_paths": supported,
            "supported_count": len(supported),
            "mcp": support["mcp"],
            "openapi": support["openapi"],
            "native": support["native"],
        }
        return TaskOutput(
            metrics=metrics,
            evidence_layer=self.evidence_layer,
            ok=True,
            raw={"support": support},
            notes=f"registration paths supported: {', '.join(supported) or 'none'}",
        )
