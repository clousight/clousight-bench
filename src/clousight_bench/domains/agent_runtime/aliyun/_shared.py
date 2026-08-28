"""Wired Aliyun AgentRun runtime provider (the real, SDK-backed implementation).

The commercial half of the open-core seam. The open core ships the
``aliyun-agentrun`` adapter as a *skeleton* (honest not-wired transport); this
package registers a ``RuntimeProviderPlugin`` for provider ``aliyun`` via the
``clousight_bench.runtime_providers`` entry point, which flips real mode to
runnable and supplies this live transport -- without editing the open adapter.

Design (see clousight-bench-pro/docs/agentrun-b-design.md and
agentrun-integration-research.md):

- Control plane (``agentrun.<region>.aliyuncs.com``): CreateAgentRuntime ->
  poll GetAgentRuntime to ready -> DeleteAgentRuntime. Drives T0.1 / T0.2.
- Data plane (``agentrun-data.<region>.aliyuncs.com``): InvokeRuntime
  (OpenAI-compatible), carrying an ``X-AgentRun-Session-ID`` header for session
  affinity; Memory API for state (T1.2); MCP activation for tools (T2.1).
  Traces go to ARMS, not a synchronous API -> CapabilityNotSupported.

The SDK (``alibabacloud-agentrun20250910``) is imported lazily inside the ops,
so this module imports (and the provider registers) even without the SDK -- the
mock path and the registration seam stay exercisable. The first real call
without the SDK raises a clear install hint, never an obscure ImportError.

Status: the CONTROL plane (create/get/delete AgentRuntime -> provision, status,
teardown) is wired against the installed SDK's typed models
(``CreateAgentRuntimeRequest`` wrapping ``CreateAgentRuntimeInput`` +
``CodeConfiguration``; ``get_agent_runtime(id, GetAgentRuntimeRequest)``;
responses read at ``body.data.{agent_runtime_id,status}``) and its request
builder passes the SDK's own ``validate()`` locally. The DATA plane (invoke /
memory / mcp) has no synchronous control-plane SDK op and is wired + validated
against a live account with a deployed agent and a public mock endpoint -- it
raises a clear ``_DataPlaneNotWired`` until then. Mock mode exercises everything
without an account.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from typing import TYPE_CHECKING, Any

from clousight_bench.core.plugin import RuntimeProviderPlugin
from clousight_bench.domains.agent_runtime import protocol
from clousight_bench.domains.agent_runtime.adapters.base import (
    Attempt,
    CapabilityNotSupported,
    ConcurrentWriteResult,
    DeprovisionResult,
    HOLResult,
    InvocationTrace,
    ProvisionResult,
    RetryStormResult,
    ScalePoint,
    ToolCall,
)
from clousight_bench.domains.agent_runtime.adapters.transport import RuntimeTransport
from clousight_bench.domains.agent_runtime.aliyun.ecs_carrier import (
    Ecs20140526Sdk,
    EcsCarrierConfig,
    EcsProbeCarrier,
)
from clousight_bench.domains.agent_runtime.campaign_probe_base import (
    CampaignProbeOrchestrator,
    _published_code_spec,
    _truthy,
)
from clousight_bench.domains.agent_runtime.session_memory import ObjectStoreSessionMemory
from clousight_bench.domains.agent_runtime.transport_base import (
    auth_headers as _auth_headers,
)
from clousight_bench.domains.agent_runtime.transport_base import (
    build_pooled_http_session,
)
from clousight_bench.domains.agent_runtime.transport_base import (
    get_probe_fns as _get_probe_fns,
)

_SDK_PACKAGE = "alibabacloud-agentrun20250910"
_READY_TIMEOUT_S = 300.0
_READY_POLL_S = 3.0


class _SdkMissing(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            f"the Aliyun AgentRun SDK is required for real-mode calls but is not "
            f"installed. Install it with: pip install {_SDK_PACKAGE} "
            f"alibabacloud-tea-openapi alibabacloud-credentials. "
            f"(Or run the adapter in mode: mock to exercise the harness without it.)"
        )


class _DataPlaneNotWired(CapabilityNotSupported):
    """Data-plane seam not yet wired for live invocation.

    Inherits CapabilityNotSupported so all task-level except-clauses that
    catch CapabilityNotSupported will also catch this (T2.1, T4.1, T4.2…).
    """

    def __init__(self, op: str) -> None:
        super().__init__(
            f"aliyun {op}: data-plane seam not yet wired for live account. "
            f"Run in mode: mock for the account-free harness."
        )


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[idx]


# Explicit export list: the split modules (state/transport/provider) import
# this shared glue by name; without __all__, linters strip the re-exports.
__all__ = [
    "Any",
    "Attempt",
    "CampaignProbeOrchestrator",
    "CapabilityNotSupported",
    "ConcurrentWriteResult",
    "DeprovisionResult",
    "Ecs20140526Sdk",
    "EcsCarrierConfig",
    "EcsProbeCarrier",
    "HOLResult",
    "InvocationTrace",
    "ObjectStoreSessionMemory",
    "ProvisionResult",
    "RetryStormResult",
    "RuntimeProviderPlugin",
    "RuntimeTransport",
    "ScalePoint",
    "TYPE_CHECKING",
    "ToolCall",
    "_DataPlaneNotWired",
    "_READY_POLL_S",
    "_READY_TIMEOUT_S",
    "_SDK_PACKAGE",
    "_SdkMissing",
    "_auth_headers",
    "_get_probe_fns",
    "_p95",
    "_published_code_spec",
    "_truthy",
    "build_pooled_http_session",
    "contextlib",
    "protocol",
    "time",
    "uuid",
]
