"""Agent-runtime domain pack.

Benchmarks the *runtime engineering* of managed agent platforms (session
hosting, tool calling, fault recovery, observability, cost attribution) --
NOT model intelligence. The tool universe is pinned by a fault-injectable
mock server so the runtime is the only variable.

v1 dimensions (5 hard, precisely reproducible tests):
    T1.2 state persistence        (planned)
    T1.3 tool-failure recovery    (implemented)
    T2.1 tool registration paths  (planned: MCP / OpenAPI / native connector)
    T4.1 trace span completeness  (planned, OpenInference schema)
    T4.2 OTel export compat       (planned)
"""
from __future__ import annotations

from clousight_bench.core.plugin import DomainPack, ProviderAdapter, Task
from clousight_bench.domains.agent_runtime.adapters.cn_clouds import (
    AliyunAgentRunAdapter,
    HuaweiAgentArtsAdapter,
    VolcengineAgentKitAdapter,
)
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter
from clousight_bench.domains.agent_runtime.tasks.t1_3_fault_recovery import FaultRecoveryTask


class AgentRuntimeDomain(DomainPack):
    domain = "agent-runtime"
    description = "Managed agent runtime platforms: sessions, tool calling, recovery, observability."

    def tasks(self) -> dict[str, type[Task]]:
        return {FaultRecoveryTask.task_id: FaultRecoveryTask}

    def adapters(self) -> dict[str, type[ProviderAdapter]]:
        return {
            LocalSimAdapter.name: LocalSimAdapter,
            AliyunAgentRunAdapter.name: AliyunAgentRunAdapter,
            HuaweiAgentArtsAdapter.name: HuaweiAgentArtsAdapter,
            VolcengineAgentKitAdapter.name: VolcengineAgentKitAdapter,
        }
