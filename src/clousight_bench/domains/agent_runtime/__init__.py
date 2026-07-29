"""Agent-runtime domain pack.

Benchmarks the *runtime engineering* of managed agent platforms (session
hosting, tool calling, fault recovery, observability, cost attribution) --
NOT model intelligence. The tool universe is pinned by a fault-injectable
mock server so the runtime is the only variable.

v1 dimensions (8 hard, precisely reproducible tests):
    T1.1 cold/warm start latency  (implemented)
    T1.2 state persistence        (implemented)
    T1.3 tool-failure recovery    (implemented)
    T2.1 tool registration paths  (implemented: MCP / OpenAPI / native connector)
    T4.1 trace span completeness  (implemented, OpenInference schema)
    T4.2 OTel export compat       (implemented)
    T5.1 cost attribution         (implemented: usage -> pricing enricher)
    T5.2 elasticity               (implemented: scaling knee under concurrency)
"""
from __future__ import annotations

from clousight_bench.core.plugin import DomainPack, ProviderAdapter, Task
from clousight_bench.domains.agent_runtime.adapters.cn_clouds import (
    AliyunAgentRunAdapter,
    HuaweiAgentArtsAdapter,
    VolcengineAgentKitAdapter,
)
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter
from clousight_bench.domains.agent_runtime.tasks.t1_1_startup_latency import StartupLatencyTask
from clousight_bench.domains.agent_runtime.tasks.t1_2_state_persistence import StatePersistenceTask
from clousight_bench.domains.agent_runtime.tasks.t1_3_fault_recovery import FaultRecoveryTask
from clousight_bench.domains.agent_runtime.tasks.t2_1_tool_registration import ToolRegistrationTask
from clousight_bench.domains.agent_runtime.tasks.t4_1_trace_completeness import TraceCompletenessTask
from clousight_bench.domains.agent_runtime.tasks.t4_2_otel_export import OtelExportTask
from clousight_bench.domains.agent_runtime.tasks.t5_1_cost_attribution import CostAttributionTask
from clousight_bench.domains.agent_runtime.tasks.t5_2_elasticity import ElasticityTask


class AgentRuntimeDomain(DomainPack):
    domain = "agent-runtime"
    description = "Managed agent runtime platforms: sessions, tool calling, recovery, observability."

    def tasks(self) -> dict[str, type[Task]]:
        return {
            StartupLatencyTask.task_id: StartupLatencyTask,
            StatePersistenceTask.task_id: StatePersistenceTask,
            FaultRecoveryTask.task_id: FaultRecoveryTask,
            ToolRegistrationTask.task_id: ToolRegistrationTask,
            TraceCompletenessTask.task_id: TraceCompletenessTask,
            OtelExportTask.task_id: OtelExportTask,
            CostAttributionTask.task_id: CostAttributionTask,
            ElasticityTask.task_id: ElasticityTask,
        }

    def adapters(self) -> dict[str, type[ProviderAdapter]]:
        return {
            LocalSimAdapter.name: LocalSimAdapter,
            AliyunAgentRunAdapter.name: AliyunAgentRunAdapter,
            HuaweiAgentArtsAdapter.name: HuaweiAgentArtsAdapter,
            VolcengineAgentKitAdapter.name: VolcengineAgentKitAdapter,
        }
