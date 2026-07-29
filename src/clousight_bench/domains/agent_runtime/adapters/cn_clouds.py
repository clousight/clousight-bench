"""Real-platform adapters: Aliyun AgentRun / Huawei AgentArts / Volcengine AgentKit.

Each is a thin declaration on top of ``ManagedAgentRuntimeAdapter``: it states
only what is cloud-specific -- the provider, the service used to template the
endpoint, the platform docs, and the minimal RAM/IAM action map per benchmark.
The shared body owns credential resolution, endpoint resolution, the mock<->real
transport switch, preflight, and the runtime-op delegation.

Status stays ``skeleton`` because the *real* transport is not wired to a live
account yet: ``csbench run`` refuses these platforms in real mode up front. But
``mode: mock`` runs them end-to-end via the shared simulated runtime, so the
whole harness (identity + endpoint + permission plumbing included) is
exercisable without an account. Wiring the real path means implementing
``NotWiredCloudTransport``'s ops against the platform SDK; it must NOT touch
tasks/ or scoring -- the runtime's own retry / session / trace behaviour is what
gets measured, so it is surfaced as observed, never re-implemented.

Common target keys (configs/agent-runtime.*.example.yaml):
    mode           "real" (default) | "mock"
    region         cloud region id (templated into the endpoint host)
    endpoint       explicit endpoint override (private / dedicated regions)
    agent_id       pre-deployed benchmark agent on the platform
    auth_env       name(s) of env vars holding credentials -- never the secret
    mock_base_url  public address of the pinned tool universe the platform agent
                   can reach (real mode only; localhost is NOT reachable from a
                   cloud runtime -- use a tunnel or a tiny cloud function)
"""
from __future__ import annotations

from typing import Any

from clousight_bench.domains.agent_runtime import permissions as perm
from clousight_bench.domains.agent_runtime.adapters.managed import ManagedAgentRuntimeAdapter


class AliyunAgentRunAdapter(ManagedAgentRuntimeAdapter):
    """Aliyun AgentRun (GA). Sessions map to AgentRun runtime sessions."""

    name = "aliyun-agentrun"
    status = "skeleton"
    provider = "aliyun"
    endpoint_service = "agentrun"
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


class HuaweiAgentArtsAdapter(ManagedAgentRuntimeAdapter):
    """Huawei Cloud AgentArts (GA)."""

    name = "huawei-agentarts"
    status = "skeleton"
    provider = "huawei"
    endpoint_service = "agentarts"
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


class VolcengineAgentKitAdapter(ManagedAgentRuntimeAdapter):
    """Volcengine AgentKit (GA). Two access modes exist (VeADK-native vs generic
    framework); results must state which mode produced them -- pass
    ``target['access_mode'] = 'veadk' | 'generic'`` and fold it into describe()."""

    name = "volcengine-agentkit"
    status = "skeleton"
    provider = "volcengine"
    endpoint_service = "agentkit"
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
        desc["access_mode"] = self.target.get("access_mode", "generic")
        return desc
