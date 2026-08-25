"""Agent-runtime domain pack.

Benchmarks the *runtime engineering* of managed agent platforms (session
hosting, tool calling, fault recovery, observability, cost attribution) --
NOT model intelligence. The tool universe is pinned by a fault-injectable
mock server so the runtime is the only variable.

Suite-first pivot: the 27 self-designed T-code dimensions were retired.
Suite-driven jobs (Sub-project B) will populate this domain via the
benchmark_suite / evaluator contract. The KEEP infra (adapters, probe/,
reaper, carriers, Terraform, mock_tools, data/, agent_bundle/) remains
intact for the SWE-bench pilot.
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


class AgentRuntimeDomain(DomainPack):
    domain = "agent-runtime"
    description = "Managed agent runtime platforms: sessions, tool calling, recovery, observability."

    def tasks(self) -> dict[str, type[Task]]:
        # Suite-first pivot: the 27 self-designed T-code dimensions were retired.
        # Suite-driven jobs (Sub-project B) will populate this via the
        # benchmark_suite / evaluator contract. No dimensions ship here today.
        return {}

    def adapters(self) -> dict[str, type[ProviderAdapter]]:
        return {
            LocalSimAdapter.name: LocalSimAdapter,
            AliyunAgentRunAdapter.name: AliyunAgentRunAdapter,
            HuaweiAgentArtsAdapter.name: HuaweiAgentArtsAdapter,
            VolcengineAgentKitAdapter.name: VolcengineAgentKitAdapter,
            AwsAgentCoreAdapter.name: AwsAgentCoreAdapter,
        }
