"""Aliyun AgentRun integration (package split of the former 2100-line module).

Public surface re-exported so the entry point
(``clousight_bench.domains.agent_runtime.aliyun:AliyunRuntimeProvider``) and
existing imports keep working: transport in ``transport.py``, session state/MCP
in ``state.py``, provider + campaign probe in ``provider.py``, shared SDK glue
in ``_shared.py``.
"""

from clousight_bench.domains.agent_runtime.aliyun.provider import (
    AliyunRuntimeProvider,
    _AliyunCampaignProbe,
)
from clousight_bench.domains.agent_runtime.aliyun.transport import AliyunAgentRunTransport

__all__ = ["AliyunAgentRunTransport", "AliyunRuntimeProvider", "_AliyunCampaignProbe"]
