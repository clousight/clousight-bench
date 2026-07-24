"""Agent-runtime adapter interface.

Tasks in this domain are written against this interface, never against a
specific cloud. A platform's fault-handling / session / trace behavior lives
entirely in its adapter, so the same task measures the *runtime*, not the model.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from clousight_bench.core.plugin import ProviderAdapter


@dataclass
class ToolCall:
    """One tool call the agent is asked to make (target = mock endpoint name)."""

    target: str  # "prices" | "inventory" | "reports"
    method: str = "GET"
    params: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)


@dataclass
class Attempt:
    """One physical attempt of a tool call (retries produce multiple attempts)."""

    call_index: int
    attempt: int
    status: int
    ok: bool
    latency_ms: float


@dataclass
class InvocationTrace:
    session_id: str
    attempts: list[Attempt]
    completed: bool
    final_state: str  # "completed" | "failed" | "aborted"


class CapabilityNotSupported(NotImplementedError):
    """A runtime does not offer a capability a task probes for.

    Raised by adapter capability methods (state persistence, tool registration,
    trace, OTel export) when the platform lacks the feature. Tasks catch this
    and record 'not supported' as a finding -- absence of a capability is
    itself a benchmark result, never a crash.
    """


class AgentRuntimeAdapter(ProviderAdapter):
    """Uniform interface every agent-runtime adapter implements.

    ``target`` keys used by real adapters (see configs/*.example.yaml):
    endpoint, region, agent_id, auth env-var names, mock_base_url.
    """

    name = "abstract-agent-runtime"

    @property
    def mock_base_url(self) -> str:
        """Where the pinned tool universe lives. Local tasks inject this."""
        return str(self.target.get("mock_base_url", "http://127.0.0.1:8770"))

    @mock_base_url.setter
    def mock_base_url(self, value: str) -> None:
        self.target["mock_base_url"] = value

    def preflight(self) -> Any:
        """Credentials + SDK (from core) plus agent-runtime specifics: for a
        real cloud run the pinned mock universe must be reachable and the
        identity must have the needed permissions. local-sim (provider-less,
        self-hosted mock) adds neither, so it always passes."""
        from clousight_bench.core import preflight as pf
        from clousight_bench.core.credentials import infer_provider

        report = super().preflight()
        provider = infer_provider(self.target, self.name)
        if provider is not None:  # real cloud platform
            report.add(pf.mock_reachable_check(str(self.target.get("mock_base_url", ""))))
            report.add(*self.check_permissions())
        return report

    def check_permissions(self) -> list[Any]:
        """Probe that the resolved identity has the permissions the tasks need.

        Default: not verified (a skeleton can't call the cloud). A wired adapter
        overrides this to make a cheap identity/authorization call (e.g. STS
        GetCallerIdentity, a dry-run describe) and return CRITICAL checks."""
        from clousight_bench.core.preflight import WARNING, Check

        return [Check("permissions", ok=True, severity=WARNING,
                      detail="not verified by this adapter",
                      remediation="wired adapters verify identity/permissions before load")]

    @abstractmethod
    def create_session(self, spec: dict[str, Any] | None = None) -> str:
        """Create a runtime session, return its id."""

    @abstractmethod
    def run_tool_plan(self, session_id: str, plan: list[ToolCall]) -> InvocationTrace:
        """Execute a sequence of tool calls under this runtime's semantics
        (including its own retry / recovery behavior on tool failure)."""

    @abstractmethod
    def destroy_session(self, session_id: str) -> None:
        """Tear down the session."""

    # --- Optional capabilities (probed by T1.2 / T2.1 / T4.1 / T4.2) ---------
    # Default = CapabilityNotSupported so an adapter opts in by overriding.
    # Real adapters must surface the platform's OWN behavior, never emulate it.

    def persist_state(self, session_id: str, state: dict[str, Any]) -> None:
        """Persist opaque session state on the runtime (T1.2)."""
        raise CapabilityNotSupported("persist_state")

    def load_state(self, session_id: str) -> dict[str, Any]:
        """Load previously persisted session state (T1.2)."""
        raise CapabilityNotSupported("load_state")

    def resume_session(self, session_id: str) -> str:
        """Simulate an interruption + resume; return the session id to use after
        resume (may equal session_id). Persisted state should survive iff the
        runtime is durable (T1.2)."""
        raise CapabilityNotSupported("resume_session")

    def register_tool(self, path: str, spec: dict[str, Any]) -> bool:
        """Register a tool via one path in {'mcp','openapi','native'}; return
        True if the runtime accepts that registration path (T2.1)."""
        raise CapabilityNotSupported("register_tool")

    def get_trace(self, session_id: str) -> list[dict[str, Any]]:
        """Return the runtime's own trace of the last invocation as
        OpenInference-shaped spans (T4.1)."""
        raise CapabilityNotSupported("get_trace")

    def export_otel(self, session_id: str) -> dict[str, Any]:
        """Return the last invocation's trace as an OTLP-compatible dict (T4.2)."""
        raise CapabilityNotSupported("export_otel")
