"""Runtime transports: the swappable back end behind one agent-runtime adapter.

The adapter is written once against this interface; ``target['mode']`` selects
the transport:

- ``MockRuntimeTransport``  -- a configurable, in-process simulated runtime with
  a deterministic fault-injectable tool universe. No cloud account. This is the
  reference behaviour local-sim has always had; sharing it means every cloud
  adapter can be dry-run end-to-end (identity + endpoint + permission plumbing
  exercised) before a single real API is wired.
- ``NotWiredCloudTransport`` -- the real-cloud seam. Every operation raises a
  clear, actionable error naming the endpoint / credential source until the
  provider's SDK calls are filled in. Filling it in must surface the runtime's
  OWN session / retry / trace behaviour, never re-implement the simulator.

Both satisfy ``RuntimeTransport`` so the adapter delegates uniformly.
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any
from urllib import request

if TYPE_CHECKING:
    from http.server import ThreadingHTTPServer

from clousight_bench.core.stats import percentiles
from clousight_bench.domains.agent_runtime import openinference
from clousight_bench.domains.agent_runtime.adapters.base import (
    Attempt,
    CancellationResult,
    CapabilityNotSupported,
    DeprovisionResult,
    InvocationTrace,
    LoadResult,
    ProvisionResult,
    RateLimitResult,
    RetentionResult,
    ScalePoint,
    SoakResult,
    ToolCall,
)


class RuntimeTransport(ABC):
    """The runtime operations an agent-runtime adapter delegates to a back end."""

    #: In-process/self-hosted mock URL once started, else None (real transports).
    mock_base_url: str | None = None

    def start(self) -> None:  # noqa: B027 - optional hook
        """Provision / connect. Default no-op."""

    def stop(self) -> None:  # noqa: B027 - optional hook
        """Release whatever start() created. Default no-op."""

    @abstractmethod
    def create_session(self, spec: dict[str, Any] | None = None) -> str: ...

    @abstractmethod
    def run_tool_plan(self, session_id: str, plan: list[ToolCall]) -> InvocationTrace: ...

    @abstractmethod
    def destroy_session(self, session_id: str) -> None: ...

    # Optional capabilities -- default to "not supported" so a back end opts in.
    def persist_state(self, session_id: str, state: dict[str, Any]) -> None:
        raise CapabilityNotSupported("persist_state")

    def load_state(self, session_id: str) -> dict[str, Any]:
        raise CapabilityNotSupported("load_state")

    def resume_session(self, session_id: str) -> str:
        raise CapabilityNotSupported("resume_session")

    def register_tool(self, path: str, spec: dict[str, Any]) -> bool:
        raise CapabilityNotSupported("register_tool")

    def get_trace(self, session_id: str) -> list[dict[str, Any]]:
        raise CapabilityNotSupported("get_trace")

    def export_otel(self, session_id: str) -> dict[str, Any]:
        raise CapabilityNotSupported("export_otel")

    def probe_scaling(self, levels: list[int]) -> list[ScalePoint]:
        raise CapabilityNotSupported("probe_scaling")

    def probe_sustained_load(self, duration_s: float, target_rps: float) -> LoadResult:
        raise CapabilityNotSupported("probe_sustained_load")

    def probe_warm_retention(self) -> RetentionResult:
        raise CapabilityNotSupported("probe_warm_retention")

    def probe_soak(self, duration_s: float) -> SoakResult:
        raise CapabilityNotSupported("probe_soak")

    def probe_rate_limit(self) -> RateLimitResult:
        raise CapabilityNotSupported("probe_rate_limit")

    def probe_cancellation(self) -> CancellationResult:
        raise CapabilityNotSupported("probe_cancellation")

    # Provisioning lifecycle (T0.1 / T0.2). Optional -> default not supported.
    def provision(self, spec: dict[str, Any] | None = None) -> ProvisionResult:
        raise CapabilityNotSupported("provision")

    def provision_status(self, runtime_id: str) -> str:
        raise CapabilityNotSupported("provision_status")

    def deprovision(self, runtime_id: str) -> DeprovisionResult:
        raise CapabilityNotSupported("deprovision")


class MockRuntimeTransport(RuntimeTransport):
    """Configurable simulated runtime over a pinned, fault-injectable tool universe.

    Every observable behaviour (recovery policy, state durability, which tool
    registration paths exist, trace completeness, OTel export) is a config knob,
    so tasks can observe both support and absence deterministically without any
    cloud account. Behaviour is intentionally identical whether it backs local-sim
    or a cloud adapter in ``mode: mock`` -- the cloud identity/endpoint plumbing
    is what differs, not the measured runtime.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        recovery = cfg.get("recovery", {})
        self.recovery_mode: str = recovery.get("mode", "auto-retry")  # "auto-retry" | "fail-fast"
        self.max_retries: int = int(recovery.get("max_retries", 3))
        self.backoff_ms: list[int] = list(recovery.get("backoff_ms", [50, 100, 200]))
        self.state_persistence: str = cfg.get("state_persistence", "durable")
        self.supported_registration_paths: list[str] = list(
            cfg.get("tool_registration", ["mcp", "openapi", "native"])
        )
        trace_cfg = cfg.get("trace", {})
        self.trace_completeness: str = trace_cfg.get("completeness", "full")  # "full" | "partial"
        self.otel_export_enabled: bool = bool(trace_cfg.get("otel_export", True))
        self.mock_port: int = int(cfg.get("mock_port", 0))
        # T1.1 startup latency: the FIRST session pays cold_ms (container spin-up),
        # every later one pays warm_ms (reuse). Default 0/0 -> no simulated penalty.
        startup = cfg.get("startup", {})
        self.cold_start_ms: float = float(startup.get("cold_ms", 0))
        self.warm_start_ms: float = float(startup.get("warm_ms", 0))
        # T5.2 elasticity model: within concurrency_limit it holds base_ms at full
        # success; beyond it, excess load is throttled and queued requests slow by
        # overload_penalty_ms per extra unit. Default: effectively unlimited.
        scaling = cfg.get("scaling", {})
        self.concurrency_limit: int = int(scaling.get("concurrency_limit", 10_000))
        self.scale_base_ms: float = float(scaling.get("base_ms", 10))
        self.overload_penalty_ms: float = float(scaling.get("overload_penalty_ms", 50))
        # T0.1/T0.2 provisioning model: provision pays ready_ms (create->ready);
        # deprovision reports clean_teardown and any residual_on_delete resources.
        provision = cfg.get("provision", {})
        self.provision_ready_ms: float = float(provision.get("ready_ms", 0))
        self.provision_clean_teardown: bool = bool(provision.get("clean_teardown", True))
        self.provision_residual: list[str] = list(provision.get("residual_on_delete", []))
        # T1.4 sustained-load model: the runtime sustains up to sustained_rps at
        # p50=base_ms, tail p99=base_ms+tail_ms; asking for more than it can
        # sustain spills into errors. Defaults model a modest healthy runtime.
        load = cfg.get("load", {})
        self.load_sustained_rps: float = float(load.get("sustained_rps", 50))
        self.load_base_ms: float = float(load.get("base_ms", 20))
        self.load_tail_ms: float = float(load.get("tail_ms", 60))
        self.load_error_rate: float = float(load.get("error_rate", 0.0))
        # T1.5 warm-pool retention: keep-alive window before an idle instance goes
        # cold again. Default: no warm pool (goes cold immediately).
        warm = cfg.get("warm", {})
        self.warm_retention_ms: float = float(warm.get("retention_ms", 0))
        self.warm_keeps_warm: bool = bool(warm.get("keeps_warm", self.warm_retention_ms > 0))
        # T1.6 soak: steady-state availability over a window. availability defaults
        # to 1 - error_rate unless set explicitly. Default: perfectly available.
        soak = cfg.get("soak", {})
        self.soak_error_rate: float = float(soak.get("error_rate", 0.0))
        self.soak_availability: float = float(
            soak.get("availability", 1.0 - self.soak_error_rate))
        self.soak_rps: float = float(soak.get("rps", 20))
        # T1.7 rate limiting: onset_rps=0 -> no throttle observed. honors_429 =
        # returns a proper 429 + Retry-After rather than silently dropping.
        rate_limit = cfg.get("rate_limit", {})
        self.rl_onset_rps: float = float(rate_limit.get("onset_rps", 0))
        self.rl_retry_after_ms: float = float(rate_limit.get("retry_after_ms", 0))
        self.rl_honors_429: bool = bool(rate_limit.get("honors_429", True))
        # T1.8 cancellation: whether a timeout/cancel is honored and still tears
        # down cleanly. Default: well-behaved (honored, teardown ran, no residual).
        cancellation = cfg.get("cancellation", {})
        self.cancel_honored: bool = bool(cancellation.get("honors_cancel", True))
        self.cancel_teardown: bool = bool(cancellation.get("teardown_on_cancel", True))
        self.cancel_residual: list[str] = list(cancellation.get("residual_on_cancel", []))
        self._session_seq = 0
        self._runtime_seq = 0
        self._mock_server: ThreadingHTTPServer | None = None
        self._state: dict[str, dict[str, Any]] = {}
        self._last_calls: dict[str, int] = {}

    @classmethod
    def from_target(cls, target: dict[str, Any]) -> MockRuntimeTransport:
        return cls(target)

    def start(self) -> None:
        """Start the pinned tool universe in-process.

        Default port 0 -> the OS assigns a free ephemeral port, so a local run
        never collides with a system service or a stale server. The port is an
        environment detail, not a tested variable, so it stays out of config_hash.
        """
        from clousight_bench.domains.agent_runtime.mock_tools import start_in_thread

        self._mock_server, _ = start_in_thread(self.mock_port)
        actual_port = self._mock_server.server_address[1]
        self.mock_base_url = f"http://127.0.0.1:{actual_port}"

    def stop(self) -> None:
        if self._mock_server is not None:
            self._mock_server.shutdown()
            self._mock_server = None

    def create_session(self, spec: dict[str, Any] | None = None) -> str:
        # First session is "cold" (container spin-up), the rest "warm" (reuse).
        delay = self.cold_start_ms if self._session_seq == 0 else self.warm_start_ms
        if delay:
            time.sleep(delay / 1000)
        self._session_seq += 1
        return f"sim-{self._session_seq}"

    def destroy_session(self, session_id: str) -> None:
        return None

    def _http(self, call: ToolCall) -> tuple[int, float]:
        base = (self.mock_base_url or "").rstrip("/")
        url = f"{base}/{call.target}"
        if call.method == "GET" and call.params:
            qs = "&".join(f"{k}={v}" for k, v in call.params.items())
            url = f"{url}?{qs}"
        data = json.dumps(call.body).encode("utf-8") if call.method == "POST" else None
        req = request.Request(url, data=data, method=call.method,
                              headers={"Content-Type": "application/json"})
        start = time.perf_counter()
        try:
            with request.urlopen(req, timeout=10) as resp:
                status = resp.status
                resp.read()
        except Exception as exc:
            # An HTTPError carries the tool's real .code (a genuine 4xx/5xx the
            # runtime should react to). Any other error is transport-level (the
            # mock unreachable / misconfigured) and maps to 599 -- a sentinel,
            # NOT an HTTP status. For real clouds, preflight's mock_reachable_check
            # catches an unreachable mock up front so this can't masquerade as a
            # runtime recovery failure mid-run.
            status = getattr(exc, "code", 599)
        return status, (time.perf_counter() - start) * 1000

    def run_tool_plan(self, session_id: str, plan: list[ToolCall]) -> InvocationTrace:
        attempts: list[Attempt] = []
        completed = True
        final_state = "completed"
        for call_index, call in enumerate(plan, start=1):
            attempt_no = 0
            while True:
                attempt_no += 1
                status, latency = self._http(call)
                ok = 200 <= status < 300
                attempts.append(Attempt(call_index, attempt_no, status, ok, round(latency, 2)))
                if ok:
                    break
                # tool failed -> apply this runtime's recovery policy
                if self.recovery_mode == "fail-fast" or attempt_no > self.max_retries:
                    completed = False
                    final_state = "aborted" if self.recovery_mode == "fail-fast" else "failed"
                    break
                backoff = self.backoff_ms[min(attempt_no - 1, len(self.backoff_ms) - 1)]
                time.sleep(backoff / 1000)
            if not completed:
                break
        self._last_calls[session_id] = len(plan)
        return InvocationTrace(session_id, attempts, completed, final_state)

    # --- Capability implementations (configurable, deterministic) ------------

    def persist_state(self, session_id: str, state: dict[str, Any]) -> None:
        self._state[session_id] = dict(state)

    def load_state(self, session_id: str) -> dict[str, Any]:
        return dict(self._state.get(session_id, {}))

    def resume_session(self, session_id: str) -> str:
        # Durable runtimes keep session state across an interruption; ephemeral
        # ones lose it. The session id is stable across resume.
        if self.state_persistence == "ephemeral":
            self._state.pop(session_id, None)
        return session_id

    def register_tool(self, path: str, spec: dict[str, Any]) -> bool:
        if path not in ("mcp", "openapi", "native"):
            raise ValueError(f"unknown registration path: {path!r}")
        return path in self.supported_registration_paths

    def get_trace(self, session_id: str) -> list[dict[str, Any]]:
        tool_calls = self._last_calls.get(session_id, 0)
        drop = ("TOOL",) if self.trace_completeness == "partial" else ()
        return openinference.build_spans(session_id, tool_calls, drop_kinds=drop)

    def export_otel(self, session_id: str) -> dict[str, Any]:
        if not self.otel_export_enabled:
            raise CapabilityNotSupported("export_otel")
        spans = self.get_trace(session_id)
        return openinference.to_otel(spans, service_name="mock-runtime")

    def probe_scaling(self, levels: list[int]) -> list[ScalePoint]:
        """Deterministic elasticity model (no randomness, replayable).

        At or below ``concurrency_limit`` the runtime serves everything at
        ``base_ms`` with full success. Beyond it, the excess is throttled
        (success_rate = served/level, served = min(level, limit)) and the served
        requests queue: their latency spreads from ``base_ms`` (first in line) up
        to ``base_ms + overload_penalty_ms * overload_ratio`` (last in line),
        where ``overload_ratio = (level - limit) / limit``. p95 is a genuine 95th
        percentile of that spread, not a repeated constant.
        """
        points: list[ScalePoint] = []
        for level in sorted(set(levels)):
            served = min(level, self.concurrency_limit)
            success = 1.0 if served >= level else round(served / level, 4)
            overload_ratio = max(0.0, (level - self.concurrency_limit) / self.concurrency_limit)
            span = self.overload_penalty_ms * overload_ratio
            # request i (0-based) waits progressively longer as the queue deepens
            latencies = [
                self.scale_base_ms + span * (i / max(served - 1, 1))
                for i in range(served)
            ]
            p95 = percentiles(latencies, ps=(95,))[95]
            points.append(ScalePoint(level, success, round(p95, 2)))
        return points

    def probe_sustained_load(self, duration_s: float, target_rps: float) -> LoadResult:
        """Deterministic sustained-load model (no randomness, replayable).

        The runtime serves up to ``sustained_rps``; asking for more spills the
        excess into errors (``overflow = (target-sustained)/target``). Served
        requests sit at ``base_ms`` (p50) with a tail out to ``base_ms+tail_ms``
        (p99); ``jitter`` is that p99-p50 spread — the predictability signal.
        """
        served_rps = min(target_rps, self.load_sustained_rps)
        overflow = max(0.0, (target_rps - self.load_sustained_rps) / target_rps) \
            if target_rps > 0 else 0.0
        error_rate = min(1.0, self.load_error_rate + overflow)
        p50 = self.load_base_ms
        p99 = self.load_base_ms + self.load_tail_ms
        requests = int(round(target_rps * duration_s))
        return LoadResult(
            throughput_rps=round(served_rps, 3),
            p50_ms=round(p50, 3),
            p99_ms=round(p99, 3),
            jitter_ms=round(p99 - p50, 3),
            error_rate=round(error_rate, 4),
            requests=requests,
            duration_s=round(duration_s, 3),
        )

    def probe_warm_retention(self) -> RetentionResult:
        """Report the configured keep-alive window (deterministic)."""
        return RetentionResult(
            retention_ms=round(self.warm_retention_ms, 3),
            keeps_warm=self.warm_keeps_warm,
        )

    def probe_soak(self, duration_s: float) -> SoakResult:
        """Report steady-state availability over a soak window (deterministic)."""
        requests = int(round(self.soak_rps * duration_s))
        return SoakResult(
            availability=round(self.soak_availability, 4),
            error_rate=round(self.soak_error_rate, 4),
            requests=requests,
            window_s=round(duration_s, 3),
        )

    def probe_rate_limit(self) -> RateLimitResult:
        """Report the configured throttling behaviour (deterministic)."""
        return RateLimitResult(
            throttle_onset_rps=round(self.rl_onset_rps, 3),
            retry_after_ms=round(self.rl_retry_after_ms, 3),
            honors_429=self.rl_honors_429,
        )

    def probe_cancellation(self) -> CancellationResult:
        """Report the configured timeout/cancel behaviour (deterministic)."""
        return CancellationResult(
            honored=self.cancel_honored,
            teardown_ran=self.cancel_teardown,
            residual=list(self.cancel_residual),
        )

    # --- Provisioning (configurable, deterministic) -------------------------

    def provision(self, spec: dict[str, Any] | None = None) -> ProvisionResult:
        """Stand up a simulated runtime instance, paying the ready_ms knob."""
        self._runtime_seq += 1
        runtime_id = f"sim-runtime-{self._runtime_seq}"
        start = time.perf_counter()
        if self.provision_ready_ms:
            time.sleep(self.provision_ready_ms / 1000)
        ready_ms = (time.perf_counter() - start) * 1000
        artifact = str((spec or {}).get("artifact_ref") or (spec or {}).get("artifact") or "")
        return ProvisionResult(
            runtime_id=runtime_id,
            ready_latency_ms=round(ready_ms, 2),
            ready=True,
            artifact_ref=artifact or "mock://artifact",
        )

    def provision_status(self, runtime_id: str) -> str:
        return "ready"

    def deprovision(self, runtime_id: str) -> DeprovisionResult:
        start = time.perf_counter()
        teardown_ms = (time.perf_counter() - start) * 1000
        return DeprovisionResult(
            teardown_ms=round(teardown_ms, 2),
            clean=self.provision_clean_teardown,
            residual=list(self.provision_residual),
        )


class NotWiredCloudTransport(RuntimeTransport):
    """Real-cloud back end seam: every op fails with a clear wiring message.

    A wired transport builds its SDK client (via ``ClientFactory``), talks to the
    resolved ``endpoint``, and surfaces the platform's own session/retry/trace
    behaviour. Until then, each operation raises with the exact prerequisites so
    the failure is actionable, never a bare NotImplementedError.
    """

    def __init__(
        self,
        adapter_name: str,
        provider: str | None,
        endpoint_url: str | None,
        client_factory: Any,
        docs: str = "",
    ) -> None:
        self.adapter_name = adapter_name
        self.provider = provider
        self.endpoint_url = endpoint_url
        self.client_factory = client_factory
        self.docs = docs

    def _not_wired(self, op: str) -> _NotWiredError:
        return _NotWiredError(self.adapter_name, op, self.endpoint_url, self.docs)

    def create_session(self, spec: dict[str, Any] | None = None) -> str:
        raise self._not_wired("create_session")

    def run_tool_plan(self, session_id: str, plan: list[ToolCall]) -> InvocationTrace:
        raise self._not_wired("run_tool_plan")

    def destroy_session(self, session_id: str) -> None:
        raise self._not_wired("destroy_session")

    # Optional capabilities must ALSO fail loud-and-clear here. If they fell
    # through to RuntimeTransport's CapabilityNotSupported, a wired-but-incomplete
    # op would be scored as "the platform lacks this capability" -- a false
    # finding. Not-wired must never masquerade as not-supported.
    def persist_state(self, session_id: str, state: dict[str, Any]) -> None:
        raise self._not_wired("persist_state")

    def load_state(self, session_id: str) -> dict[str, Any]:
        raise self._not_wired("load_state")

    def resume_session(self, session_id: str) -> str:
        raise self._not_wired("resume_session")

    def register_tool(self, path: str, spec: dict[str, Any]) -> bool:
        raise self._not_wired("register_tool")

    def get_trace(self, session_id: str) -> list[dict[str, Any]]:
        raise self._not_wired("get_trace")

    def export_otel(self, session_id: str) -> dict[str, Any]:
        raise self._not_wired("export_otel")

    def probe_scaling(self, levels: list[int]) -> list[ScalePoint]:
        raise self._not_wired("probe_scaling")

    def probe_sustained_load(self, duration_s: float, target_rps: float) -> LoadResult:
        raise self._not_wired("probe_sustained_load")

    def probe_warm_retention(self) -> RetentionResult:
        raise self._not_wired("probe_warm_retention")

    def probe_soak(self, duration_s: float) -> SoakResult:
        raise self._not_wired("probe_soak")

    def probe_rate_limit(self) -> RateLimitResult:
        raise self._not_wired("probe_rate_limit")

    def probe_cancellation(self) -> CancellationResult:
        raise self._not_wired("probe_cancellation")

    def provision(self, spec: dict[str, Any] | None = None) -> ProvisionResult:
        raise self._not_wired("provision")

    def provision_status(self, runtime_id: str) -> str:
        raise self._not_wired("provision_status")

    def deprovision(self, runtime_id: str) -> DeprovisionResult:
        raise self._not_wired("deprovision")


class _NotWiredError(NotImplementedError):
    def __init__(self, adapter: str, op: str, endpoint: str | None, docs: str) -> None:
        super().__init__(
            f"{adapter}.{op}: real-cloud transport not wired. "
            f"Wire it by building the SDK client (ClientFactory) against endpoint "
            f"{endpoint or '<unresolved>'}, then implementing this op to surface the "
            f"runtime's own behaviour. You also need credentials in env vars and a "
            f"publicly reachable mock_base_url. Docs: {docs or 'n/a'}. "
            f"Or run in mode: mock to exercise the harness without an account."
        )
