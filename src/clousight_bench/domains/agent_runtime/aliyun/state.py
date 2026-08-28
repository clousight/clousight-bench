"""Aliyun OSS-backed session memory + MCP integration."""

from __future__ import annotations

from clousight_bench.domains.agent_runtime.aliyun._shared import (
    Any,
    CapabilityNotSupported,
    ObjectStoreSessionMemory,
)


class _LiveMemory(ObjectStoreSessionMemory):
    """OSS-backed session state for AgentRun.

    AgentRun's Memory Collection API is a RAG/vector store, not a plain K/V, so
    T1.2 (does state persist across sessions?) uses the OSS bucket the adapter
    already has. This is the Aliyun binding of ``ObjectStoreSessionMemory`` over
    ``Oss2Client`` (public endpoint, shared credential chain); the key layout and
    store/fetch/cleanup live in the base class. State files are cleaned up at
    teardown.
    """

    def __init__(self, bucket: str, region: str, run_id: str | None = None) -> None:
        from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client

        super().__init__(Oss2Client(bucket, region), run_id)


class _LiveMcp:
    """AgentRun MCP: template-based, no dynamic tool registration.

    AgentRun's MCP activates a pre-registered template via ActivateTemplateMCP.
    T2.1's _TOOL_SPEC is an arbitrary tool definition that does not map to a
    registered template name, so this path is reported as CapabilityNotSupported.
    That is a faithful record of the platform's real behaviour, not a bug.
    """

    def __init__(self, client_factory: Any = None) -> None:
        self._client_factory = client_factory  # inject a real SDK client (testable)

    def activate(self, name: str, spec: dict[str, Any]) -> bool:
        if self._client_factory is None:
            raise CapabilityNotSupported(
                "register_tool[mcp]: AgentRun MCP uses pre-registered templates "
                "(ActivateTemplateMCP); dynamic tool registration is not supported. "
                "Pre-create an MCP template in the AgentRun console before calling."
            )
        # Try to activate a template of the same name; a missing or malformed
        # template counts as CapabilityNotSupported.
        try:
            from alibabacloud_agentrun20250910 import models as m

            self._client_factory().activate_template_mcp(
                name,
                m.ActivateTemplateMCPRequest(transport="sse"),  # transport is required
            )
            return True
        except Exception as exc:
            err = str(exc)
            # Template not found -> it must be pre-registered.
            if any(k in err for k in ("NotFound", "not found", "NoSuch", "ERR_NOT_FOUND")):
                raise CapabilityNotSupported(
                    f"register_tool[mcp]: AgentRun MCP uses pre-registered templates; "
                    f"template '{name}' does not exist. Create it in the console and retry."
                ) from exc
            # Other 400/403 -> platform restriction, also CapabilityNotSupported.
            if "400" in err or "403" in err:
                raise CapabilityNotSupported(
                    f"register_tool[mcp]: AgentRun MCP template activation restricted — {err[:120]}"
                ) from exc
            raise
