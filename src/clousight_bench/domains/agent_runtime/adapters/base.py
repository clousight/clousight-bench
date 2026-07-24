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

    # Per-cloud map: abstract capability token -> concrete minimal cloud actions.
    # Each real adapter overrides this; local-sim leaves it empty (no cloud perms).
    PERMISSION_MAP: dict[str, list[str]] = {}

    def preflight(self, task: Any | None = None) -> Any:
        """Credentials + SDK (from core) plus agent-runtime specifics: for a
        real cloud run the pinned mock universe must be reachable and the
        identity must have the *minimal permissions this specific benchmark
        needs*. local-sim (provider-less, self-hosted mock) adds neither, so it
        always passes."""
        from clousight_bench.core import preflight as pf
        from clousight_bench.core.credentials import infer_provider

        report = super().preflight(task)
        provider = infer_provider(self.target, self.name)
        if provider is not None:  # real cloud platform
            report.add(pf.mock_reachable_check(str(self.target.get("mock_base_url", ""))))
            report.add(*self.check_permissions(task))
        return report

    def required_actions(self, task: Any | None) -> tuple[list[str], list[str]]:
        """Map a task's abstract permission tokens to this cloud's concrete
        minimal actions. Returns (actions, unmapped_tokens)."""
        tokens = list(getattr(task, "required_permissions", ()) or ())
        actions: list[str] = []
        unmapped: list[str] = []
        for token in tokens:
            mapped = self.PERMISSION_MAP.get(token)
            if mapped is None:
                unmapped.append(token)
            else:
                actions.extend(mapped)
        return list(dict.fromkeys(actions)), unmapped  # dedupe, preserve order

    def _probe_permissions(self, actions: list[str]) -> tuple[bool, list[str]] | None:
        """Verify the resolved identity actually holds ``actions``.

        Return (ok, missing) after a cheap dry-run / authorization-simulation
        call, or None if this adapter cannot verify (skeleton). A wired adapter
        overrides this (e.g. AWS ``iam:SimulatePrincipalPolicy`` / a dry-run
        describe; Aliyun RAM policy check)."""
        return None

    def check_permissions(self, task: Any | None = None) -> list[Any]:
        """Check exactly the minimal permissions this benchmark needs on this
        cloud. The required set is a (benchmark x cloud) mapping: the task's
        capability tokens resolved through this adapter's PERMISSION_MAP."""
        from clousight_bench.core.preflight import CRITICAL, WARNING, Check

        if task is None:
            return [Check("permissions", ok=True, severity=WARNING,
                          detail="no task context (run-level check only)")]
        actions, unmapped = self.required_actions(task)
        checks: list[Any] = []
        if unmapped:
            checks.append(Check("permissions:mapping", ok=False, severity=WARNING,
                                detail=f"no {self.name} mapping for tokens {unmapped}",
                                remediation="add these to the adapter's PERMISSION_MAP"))
        label = f"permissions[{getattr(task, 'task_id', '?')}]"
        probe = self._probe_permissions(actions)
        if probe is None:  # skeleton: surface the minimal action list, don't block
            checks.append(Check(label, ok=True, severity=WARNING,
                                detail=f"needs {actions or 'none'} — not verified by this adapter",
                                remediation="a wired adapter verifies via dry-run/policy simulation"))
        else:
            ok, missing = probe
            if ok:
                checks.append(Check(label, ok=True, severity=CRITICAL,
                                    detail=f"identity holds {actions or 'none'}"))
            else:
                checks.append(Check(label, ok=False, severity=CRITICAL,
                                    detail=f"missing {missing}",
                                    remediation=f"grant the identity: {', '.join(missing)}"))
        return checks

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
