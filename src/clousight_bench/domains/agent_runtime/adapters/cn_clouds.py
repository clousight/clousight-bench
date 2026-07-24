"""Phase-1 real-platform adapters: Aliyun AgentRun / Huawei AgentArts / Volcengine AgentKit.

These are honest skeletons: each documents its target contract and fails with a
clear message until wired to a real account. Filling one in must NOT touch
tasks/ or scoring -- the runtime's own retry / session / trace behavior is
what gets measured, so it must be surfaced as observed, never reimplemented.

Common target keys (configs/agent-runtime.*.example.yaml):
    endpoint       platform invoke endpoint or region endpoint
    region         cloud region id
    agent_id       pre-deployed benchmark agent on the platform
    auth_env       name(s) of env vars holding credentials -- never the secret itself
    mock_base_url  public address of the pinned tool universe the platform agent
                   can reach (the mock server must be exposed, e.g. via a tunnel
                   or a tiny cloud function; localhost is NOT reachable from a
                   cloud runtime)
"""
from __future__ import annotations

from typing import Any

from clousight_bench.domains.agent_runtime import permissions as perm
from clousight_bench.domains.agent_runtime.adapters.base import (
    AgentRuntimeAdapter,
    InvocationTrace,
    ToolCall,
)


class _NotWiredError(NotImplementedError):
    def __init__(self, adapter: str, doc: str) -> None:
        super().__init__(
            f"{adapter} is a skeleton: wire it to your own account first. "
            f"You need credentials in env vars, a deployed benchmark agent, and a "
            f"publicly reachable mock_base_url. Platform docs: {doc}"
        )


class AliyunAgentRunAdapter(AgentRuntimeAdapter):
    """Aliyun AgentRun (GA). Sessions map to AgentRun runtime sessions."""

    name = "aliyun-agentrun"
    DOCS = "https://help.aliyun.com/ (AgentRun)"
    # Abstract capability token -> Aliyun RAM action(s) (minimal per benchmark).
    PERMISSION_MAP = {
        perm.SESSION_CREATE: ["agentrun:CreateSession", "agentrun:DeleteSession"],
        perm.SESSION_STATE: ["agentrun:PutSessionState", "agentrun:GetSessionState"],
        perm.TOOL_INVOKE: ["agentrun:InvokeAgent"],
        perm.TOOL_REGISTER: ["agentrun:RegisterTool"],
        perm.TRACE_READ: ["agentrun:GetTrace"],
        perm.TRACE_EXPORT: ["agentrun:ExportTrace"],
    }

    def create_session(self, spec: dict[str, Any] | None = None) -> str:
        raise _NotWiredError(self.name, self.DOCS)

    def run_tool_plan(self, session_id: str, plan: list[ToolCall]) -> InvocationTrace:
        raise _NotWiredError(self.name, self.DOCS)

    def destroy_session(self, session_id: str) -> None:
        raise _NotWiredError(self.name, self.DOCS)


class HuaweiAgentArtsAdapter(AgentRuntimeAdapter):
    """Huawei Cloud AgentArts (GA)."""

    name = "huawei-agentarts"
    DOCS = "https://support.huaweicloud.com/ (AgentArts)"
    # Abstract capability token -> Huawei IAM action(s) (minimal per benchmark).
    PERMISSION_MAP = {
        perm.SESSION_CREATE: ["agentarts:session:create", "agentarts:session:delete"],
        perm.SESSION_STATE: ["agentarts:session:putState", "agentarts:session:getState"],
        perm.TOOL_INVOKE: ["agentarts:agent:invoke"],
        perm.TOOL_REGISTER: ["agentarts:tool:register"],
        perm.TRACE_READ: ["agentarts:trace:get"],
        perm.TRACE_EXPORT: ["agentarts:trace:export"],
    }

    def create_session(self, spec: dict[str, Any] | None = None) -> str:
        raise _NotWiredError(self.name, self.DOCS)

    def run_tool_plan(self, session_id: str, plan: list[ToolCall]) -> InvocationTrace:
        raise _NotWiredError(self.name, self.DOCS)

    def destroy_session(self, session_id: str) -> None:
        raise _NotWiredError(self.name, self.DOCS)


class VolcengineAgentKitAdapter(AgentRuntimeAdapter):
    """Volcengine AgentKit (GA). Two access modes exist (VeADK-native vs generic
    framework); results must state which mode produced them -- pass
    ``target['mode'] = 'veadk' | 'generic'`` and fold it into describe()."""

    name = "volcengine-agentkit"
    DOCS = "https://www.volcengine.com/docs/86681 (AgentKit)"
    # Abstract capability token -> Volcengine IAM action(s) (minimal per benchmark).
    PERMISSION_MAP = {
        perm.SESSION_CREATE: ["agentkit:CreateSession", "agentkit:DeleteSession"],
        perm.SESSION_STATE: ["agentkit:PutSessionState", "agentkit:GetSessionState"],
        perm.TOOL_INVOKE: ["agentkit:InvokeAgent"],
        perm.TOOL_REGISTER: ["agentkit:RegisterTool"],
        perm.TRACE_READ: ["agentkit:GetTrace"],
        perm.TRACE_EXPORT: ["agentkit:ExportTrace"],
    }

    def describe(self) -> dict[str, Any]:
        desc = super().describe()
        desc["access_mode"] = self.target.get("mode", "generic")
        return desc

    def create_session(self, spec: dict[str, Any] | None = None) -> str:
        raise _NotWiredError(self.name, self.DOCS)

    def run_tool_plan(self, session_id: str, plan: list[ToolCall]) -> InvocationTrace:
        raise _NotWiredError(self.name, self.DOCS)

    def destroy_session(self, session_id: str) -> None:
        raise _NotWiredError(self.name, self.DOCS)
