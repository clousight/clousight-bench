"""Agent-runtime adapter interface.

Tasks in this domain are written against this interface, never against a
specific cloud. A platform's fault-handling / session / trace behavior lives
entirely in its adapter, so the same task measures the *runtime*, not the model.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from clousight_bench.core.observation import ObservationBundle
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


@dataclass
class ScalePoint:
    """One point on an elasticity curve: behaviour at a given concurrency level."""

    concurrency: int
    success_rate: float  # 0.0..1.0 of invocations that succeeded at this level
    p95_ms: float  # 95th-percentile latency observed at this level
    observed_instances: int | None = None  # actual instance count post-burst (None if unsupported)


@dataclass
class LoadResult:
    """Behaviour under sustained steady load (the throughput / tail dimension, T1.4)."""

    throughput_rps: float  # sustained requests/sec actually served
    p50_ms: float  # median request latency under load
    p99_ms: float  # 99th-percentile (tail) latency under load
    jitter_ms: float  # latency spread (p99 - p50), the predictability signal
    error_rate: float  # 0.0..1.0 of requests that failed under load
    requests: int  # total requests issued during the window
    duration_s: float  # observation window length
    # Error breakdown (optional; default=0.0 when adapter doesn't disaggregate).
    # transport_error_rate: SSL / connection failures before the runtime was reached.
    # runtime_error_rate: AgentRun data-plane returned non-2xx HTTP (exception with HTTP status 4xx/5xx).
    # tool_error_rate: AgentRun invoke succeeded, mock tool returned ok=False (mock-server capacity issue).
    transport_error_rate: float = 0.0
    runtime_error_rate: float = 0.0
    tool_error_rate: float = 0.0  # 0..1 mock-tool failures (AgentRun OK, tool returned error)


@dataclass
class RetentionResult:
    """How long an idle instance stays warm before going cold again (T1.5)."""

    retention_ms: float  # keep-alive window: idle time before the next start is cold
    keeps_warm: bool  # whether the runtime keeps any warm instance at all


@dataclass
class SoakResult:
    """Steady-state availability over a soak window (the reliability dimension, T1.6)."""

    availability: float  # 0.0..1.0 of the window the runtime served successfully
    error_rate: float  # 0.0..1.0 of requests that failed during the window
    requests: int  # total requests issued during the soak
    window_s: float  # soak window length


@dataclass
class RateLimitResult:
    """How the runtime throttles once demand exceeds quota (T1.7)."""

    throttle_onset_rps: float  # rps at which throttling begins (0 = none observed)
    retry_after_ms: float  # advertised Retry-After when throttled (0 = none)
    honors_429: bool  # returns a proper 429 + Retry-After rather than dropping


@dataclass
class CancellationResult:
    """Whether a timed-out / cancelled request is cleanly torn down (T1.8)."""

    honored: bool  # the cancel/timeout actually stopped the work
    teardown_ran: bool  # cleanup still ran after the cancel (no orphaned work)
    residual: list[str] = field(default_factory=list)  # resources leaked by the cancel


@dataclass
class SignalsResult:
    """Metrics & log completeness beyond traces (the observability dimension, T4.3)."""

    metrics_present: int  # distinct metric signals actually exported
    metrics_expected: int  # metric signals a complete runtime should export
    logs_present: int  # log records actually exported
    logs_expected: int  # log records a complete runtime should export
    structured_logs: bool  # whether logs are structured (queryable), not free text


@dataclass
class PropagationResult:
    """Trace parent/child correctness across tool calls (T4.4)."""

    spans: int  # total spans in the trace
    orphan_spans: int  # spans whose parent id points nowhere (broken context)
    root_count: int  # number of root spans (a clean trace has exactly one)


@dataclass
class ExportLatencyResult:
    """How fast telemetry lands and whether any is dropped (T4.5)."""

    export_latency_ms: float  # emit -> visible-in-backend latency
    dropped_ratio: float  # 0.0..1.0 of spans/metrics lost on export


@dataclass
class IdleCostResult:
    """Cost while warm-but-idle and whether the runtime scales to zero (T5.3)."""

    scales_to_zero: bool  # whether an idle runtime bills nothing
    idle_cost_per_hour: float  # cost per hour of a warm-but-idle instance


@dataclass
class IsolationResult:
    """Tenant isolation / sandbox strength (the security dimension, T6.1)."""

    tenant_isolated: bool  # workloads of different tenants are isolated
    network_egress_controlled: bool  # outbound network is restricted by default
    filesystem_isolated: bool  # the workload filesystem is private/ephemeral
    # Dimensions whose value comes from platform documentation, not live measurement.
    # Adapters that hardcode a dimension list its name here so the scorer can apply
    # evidence="A" instead of evidence="B" and exclude it from measured_score.
    platform_asserted_dimensions: list = field(default_factory=list)


@dataclass
class CeilingResult:
    """The concurrency ceiling: max in-flight the runtime admits (T5.4)."""

    max_in_flight: int  # highest concurrent invocations admitted
    hard_limit: bool  # whether the ceiling is a hard cap (vs soft/burstable)


@dataclass
class ProvisionResult:
    """Outcome of standing up a runtime instance (the deploy dimension, T0.1)."""

    runtime_id: str
    ready_latency_ms: float  # create -> ready (the cold provisioning cost)
    ready: bool
    artifact_ref: str = ""  # what was deployed (code package / image ref), for reproducibility
    tags: dict[str, Any] = field(default_factory=dict)  # tags stamped on the resource


@dataclass
class DeprovisionResult:
    """Outcome of tearing a runtime instance down (the teardown dimension, T0.2)."""

    teardown_ms: float
    clean: bool  # True iff nothing was left behind
    residual: list[str] = field(default_factory=list)  # ids of any leaked/residual resources


@dataclass
class RetryStormResult:
    """Whether all-call-failure triggers abort-on-first or infinite retry (T1.10)."""

    storm_behavior: str  # "abort_on_first_failure" | "timeout_loop" | "unexpected_success"
    calls_attempted: int  # physical tool call attempts before abort/timeout
    duration_ms: float  # wall time of the probe window


@dataclass
class ConcurrentWriteResult:
    """Whether two simultaneous writes to the same state key corrupt it (T1.11)."""

    write_safe: bool  # True if the final value is one of the two written values
    winner: str  # "session_a" | "session_b" | "unknown"


@dataclass
class HOLResult:
    """Whether a slow request blocks fast ones sharing the same session queue (T1.12)."""

    blocked: bool  # True if fast_p99 > slow_p50 * 0.5
    fast_p50_ms: float  # median latency of the fast requests
    slow_p50_ms: float  # median latency of the slow request
    hol_ratio: float  # fast_p99 / slow_p50 (> 0.5 = blocked)


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
    def session_cold_start_is_provision(self) -> bool:
        """True when ``create_session`` has no cloud round-trip (session ID is
        client-local). Cold-start cost lives in T0.1 (provision), not T1.1.
        Override in transports where session creation is a cheap local operation."""
        return False

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
            if getattr(task, "requires_mock_server", True):
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
            return [
                Check(
                    "permissions", ok=True, severity=WARNING, detail="no task context (run-level check only)"
                )
            ]
        actions, unmapped = self.required_actions(task)
        checks: list[Any] = []
        if unmapped:
            checks.append(
                Check(
                    "permissions:mapping",
                    ok=False,
                    severity=WARNING,
                    detail=f"no {self.name} mapping for tokens {unmapped}",
                    remediation="add these to the adapter's PERMISSION_MAP",
                )
            )
        label = f"permissions[{getattr(task, 'task_id', '?')}]"
        probe = self._probe_permissions(actions)
        if probe is None:  # skeleton: surface the minimal action list, don't block
            checks.append(
                Check(
                    label,
                    ok=True,
                    severity=WARNING,
                    detail=f"needs {actions or 'none'} — not verified by this adapter",
                    remediation="a wired adapter verifies via dry-run/policy simulation",
                )
            )
        else:
            ok, missing = probe
            if ok:
                checks.append(
                    Check(label, ok=True, severity=CRITICAL, detail=f"identity holds {actions or 'none'}")
                )
            else:
                checks.append(
                    Check(
                        label,
                        ok=False,
                        severity=CRITICAL,
                        detail=f"missing {missing}",
                        remediation=f"grant the identity: {', '.join(missing)}",
                    )
                )
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

    def run_data_plane_probe(self, name: str, params: dict[str, Any] | None = None) -> ObservationBundle:
        """Dispatch a data-plane probe by name, returning an ObservationBundle.

        Default: run the shared packer (call probe_<name>, pack). A real adapter
        may override to route the whole measurement to an in-region probe.
        """
        from clousight_bench.domains.agent_runtime.dataplane_dispatch import (
            run_data_plane_probe as _dispatch,
        )

        return _dispatch(self, name, params)

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

    def probe_scaling(self, levels: list[int]) -> list[ScalePoint]:
        """Report elasticity: success rate + p95 latency at each concurrency level
        (T5.2). A real adapter actually drives concurrent load and measures; it
        must surface the platform's OWN behaviour under load, never model it."""
        raise CapabilityNotSupported("probe_scaling")

    def probe_sustained_load(self, duration_s: float, target_rps: float) -> LoadResult:
        """Report sustained throughput + tail latency under steady load (T1.4). A
        real adapter drives ``target_rps`` for ``duration_s`` and measures the
        platform's OWN throughput / p50 / p99 / error rate, never models it."""
        raise CapabilityNotSupported("probe_sustained_load")

    def probe_warm_retention(self) -> RetentionResult:
        """Report the keep-alive window: how long an idle instance stays warm
        before the next start pays a cold penalty again (T1.5)."""
        raise CapabilityNotSupported("probe_warm_retention")

    def probe_soak(self, duration_s: float) -> SoakResult:
        """Report steady-state availability + error rate over a soak window (T1.6).
        A real adapter runs continuous traffic for ``duration_s`` and measures the
        platform's OWN availability, never models it."""
        raise CapabilityNotSupported("probe_soak")

    def probe_rate_limit(self) -> RateLimitResult:
        """Report throttling behaviour once demand exceeds quota (T1.7): the onset
        rps, advertised Retry-After, and whether a proper 429 is returned."""
        raise CapabilityNotSupported("probe_rate_limit")

    def probe_ttft(self) -> float:
        """Time-to-first-token via streaming invoke (T1.9).

        Fire a single tool call with ``stream=True`` and return the milliseconds
        elapsed from sending the request to receiving the first non-empty SSE
        ``data:`` chunk. A runtime that does not support streaming invoke returns
        the full round-trip latency (RTT) as a best-effort proxy.
        CapabilityNotSupported = the transport cannot issue streaming requests at all."""
        raise CapabilityNotSupported("probe_ttft")

    def probe_cancellation(self) -> CancellationResult:
        """Report whether a timed-out / cancelled request is honored and still
        torn down cleanly, leaving nothing orphaned (T1.8)."""
        raise CapabilityNotSupported("probe_cancellation")

    def probe_signals(self) -> SignalsResult:
        """Report metrics & log completeness beyond traces (T4.3): how many of
        the expected metric/log signals the runtime actually exports."""
        raise CapabilityNotSupported("probe_signals")

    def probe_span_propagation(self) -> PropagationResult:
        """Report trace parent/child correctness (T4.4): orphaned spans and the
        root-span count, i.e. whether context propagates across tool calls."""
        raise CapabilityNotSupported("probe_span_propagation")

    def probe_export_latency(self) -> ExportLatencyResult:
        """Report telemetry export latency and drop ratio (T4.5): how fast spans
        land in the backend and whether any are lost."""
        raise CapabilityNotSupported("probe_export_latency")

    def probe_idle_cost(self) -> IdleCostResult:
        """Report idle / scale-to-zero cost (T5.3): whether a warm-but-idle
        instance bills, and how much per hour."""
        raise CapabilityNotSupported("probe_idle_cost")

    def probe_isolation(self) -> IsolationResult:
        """Report tenant isolation / sandbox strength (T6.1): tenant, network
        egress, and filesystem isolation."""
        raise CapabilityNotSupported("probe_isolation")

    def probe_concurrency_ceiling(self) -> CeilingResult:
        """Report the concurrency ceiling (T5.4): max in-flight admitted and
        whether it is a hard cap."""
        raise CapabilityNotSupported("probe_concurrency_ceiling")

    def probe_fault_recovery(self, fault_call_index: int = 3) -> InvocationTrace:
        """Run the T1.3 request-level fault injection probe.

        Encodes the fault spec in each request body (fail_after_n_calls=fault_call_index)
        so the deployed agent returns a synthetic failure on the Nth call without
        relying on shared mock-server state. Transports that support this method
        override it; the default raises CapabilityNotSupported so the task can
        fall back to the legacy mock-server fault path when the transport has not
        yet implemented this method.
        """
        raise CapabilityNotSupported("probe_fault_recovery")

    def probe_retry_storm(self, max_window_s: float = 30.0) -> RetryStormResult:
        """Run the T1.10 retry-storm probe.

        Injects a persistent fault (every call fails) on a 5-call plan and
        observes whether the runtime aborts cleanly on the first failure or
        loops indefinitely until the window expires.
        """
        raise CapabilityNotSupported("probe_retry_storm")

    def probe_concurrent_writes(self) -> ConcurrentWriteResult:
        """Run the T1.11 concurrent state-write probe.

        Two sessions simultaneously write to the same state key; the result
        must be one of the two written values (last-writer-wins, no corruption).
        """
        raise CapabilityNotSupported("probe_concurrent_writes")

    def probe_hol_blocking(self) -> HOLResult:
        """Run the T1.12 head-of-line blocking probe.

        Fires 1 slow request and 5 fast requests concurrently on the same
        session. If the fast requests are delayed nearly as long as the slow
        one, the runtime has HOL-blocking in its session queue.
        """
        raise CapabilityNotSupported("probe_hol_blocking")

    # --- Provisioning: the deploy / teardown lifecycle (T0.1 / T0.2) ---------
    # An always-on runtime instance is stood up before it can host sessions and
    # torn down after. Default = CapabilityNotSupported so an adapter opts in; a
    # real adapter surfaces the platform's OWN CreateRuntime->ready and
    # Delete->clean behaviour (timed / verified), never fabricates it.

    def provision(self, spec: dict[str, Any] | None = None) -> ProvisionResult:
        """Stand up a runtime instance from an artifact; time create->ready (T0.1)."""
        raise CapabilityNotSupported("provision")

    def provision_status(self, runtime_id: str) -> str:
        """Current lifecycle state of a runtime instance (e.g. 'ready')."""
        raise CapabilityNotSupported("provision_status")

    def deprovision(self, runtime_id: str) -> DeprovisionResult:
        """Tear a runtime instance down; report whether teardown was clean (T0.2)."""
        raise CapabilityNotSupported("deprovision")
