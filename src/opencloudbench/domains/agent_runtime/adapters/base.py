"""Agent-runtime adapter interface.

Tasks in this domain are written against this interface, never against a
specific cloud. A platform's fault-handling / session / trace behavior lives
entirely in its adapter, so the same task measures the *runtime*, not the model.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from opencloudbench.core.plugin import ProviderAdapter


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
