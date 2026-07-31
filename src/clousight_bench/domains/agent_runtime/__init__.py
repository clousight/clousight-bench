"""Agent-runtime domain pack.

Benchmarks the *runtime engineering* of managed agent platforms (session
hosting, tool calling, fault recovery, observability, cost attribution) --
NOT model intelligence. The tool universe is pinned by a fault-injectable
mock server so the runtime is the only variable.

v1 dimensions (precisely reproducible tests):
    T0.1 provisioning (deploy) latency (implemented)
    T0.2 teardown cleanliness          (implemented)
    T1.1 cold/warm start latency  (implemented)
    T1.4 sustained load & tail    (implemented: throughput / p99 / jitter)
    T1.5 warm-pool retention      (implemented: keep-alive window)
    T1.2 state persistence        (implemented)
    T1.3 tool-failure recovery    (implemented)
    T1.6 soak availability        (implemented: steady-state availability/error rate)
    T1.7 rate limiting            (implemented: throttle onset + 429 contract)
    T1.8 timeout & cancellation   (implemented: honored + teardown-on-cancel)
    T2.1 tool registration paths  (implemented: MCP / OpenAPI / native connector)
    T4.1 trace span completeness  (implemented, OpenInference schema)
    T4.2 OTel export compat       (implemented)
    T4.3 metrics & log signals    (implemented: completeness beyond traces)
    T4.4 span propagation         (implemented: orphans + root count)
    T4.5 export latency           (implemented: emit->visible + drop ratio)
    T5.1 cost attribution         (implemented: usage -> pricing enricher)
    T5.2 elasticity               (implemented: scaling knee under concurrency)
"""
from __future__ import annotations

from clousight_bench.core.plugin import DomainPack, ProviderAdapter, Task
from clousight_bench.domains.agent_runtime.adapters.aws_clouds import AwsAgentCoreAdapter
from clousight_bench.domains.agent_runtime.adapters.cn_clouds import (
    AliyunAgentRunAdapter,
    HuaweiAgentArtsAdapter,
    VolcengineAgentKitAdapter,
)
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter
from clousight_bench.domains.agent_runtime.tasks.t0_1_provision_latency import ProvisionLatencyTask
from clousight_bench.domains.agent_runtime.tasks.t0_2_teardown_cleanliness import (
    TeardownCleanlinessTask,
)
from clousight_bench.domains.agent_runtime.tasks.t1_1_startup_latency import StartupLatencyTask
from clousight_bench.domains.agent_runtime.tasks.t1_2_state_persistence import StatePersistenceTask
from clousight_bench.domains.agent_runtime.tasks.t1_3_fault_recovery import FaultRecoveryTask
from clousight_bench.domains.agent_runtime.tasks.t1_4_sustained_load import SustainedLoadTask
from clousight_bench.domains.agent_runtime.tasks.t1_5_warm_retention import WarmRetentionTask
from clousight_bench.domains.agent_runtime.tasks.t1_6_soak import SoakTask
from clousight_bench.domains.agent_runtime.tasks.t1_7_rate_limit import RateLimitTask
from clousight_bench.domains.agent_runtime.tasks.t1_8_cancellation import CancellationTask
from clousight_bench.domains.agent_runtime.tasks.t2_1_tool_registration import ToolRegistrationTask
from clousight_bench.domains.agent_runtime.tasks.t4_1_trace_completeness import TraceCompletenessTask
from clousight_bench.domains.agent_runtime.tasks.t4_2_otel_export import OtelExportTask
from clousight_bench.domains.agent_runtime.tasks.t4_3_signals import SignalCompletenessTask
from clousight_bench.domains.agent_runtime.tasks.t4_4_span_propagation import SpanPropagationTask
from clousight_bench.domains.agent_runtime.tasks.t4_5_export_latency import ExportLatencyTask
from clousight_bench.domains.agent_runtime.tasks.t5_1_cost_attribution import CostAttributionTask
from clousight_bench.domains.agent_runtime.tasks.t5_2_elasticity import ElasticityTask


class AgentRuntimeDomain(DomainPack):
    domain = "agent-runtime"
    description = "Managed agent runtime platforms: sessions, tool calling, recovery, observability."

    def tasks(self) -> dict[str, type[Task]]:
        return {
            ProvisionLatencyTask.task_id: ProvisionLatencyTask,
            TeardownCleanlinessTask.task_id: TeardownCleanlinessTask,
            StartupLatencyTask.task_id: StartupLatencyTask,
            SustainedLoadTask.task_id: SustainedLoadTask,
            WarmRetentionTask.task_id: WarmRetentionTask,
            StatePersistenceTask.task_id: StatePersistenceTask,
            FaultRecoveryTask.task_id: FaultRecoveryTask,
            SoakTask.task_id: SoakTask,
            RateLimitTask.task_id: RateLimitTask,
            CancellationTask.task_id: CancellationTask,
            ToolRegistrationTask.task_id: ToolRegistrationTask,
            TraceCompletenessTask.task_id: TraceCompletenessTask,
            OtelExportTask.task_id: OtelExportTask,
            SignalCompletenessTask.task_id: SignalCompletenessTask,
            SpanPropagationTask.task_id: SpanPropagationTask,
            ExportLatencyTask.task_id: ExportLatencyTask,
            CostAttributionTask.task_id: CostAttributionTask,
            ElasticityTask.task_id: ElasticityTask,
        }

    def adapters(self) -> dict[str, type[ProviderAdapter]]:
        return {
            LocalSimAdapter.name: LocalSimAdapter,
            AliyunAgentRunAdapter.name: AliyunAgentRunAdapter,
            HuaweiAgentArtsAdapter.name: HuaweiAgentArtsAdapter,
            VolcengineAgentKitAdapter.name: VolcengineAgentKitAdapter,
            AwsAgentCoreAdapter.name: AwsAgentCoreAdapter,
        }
