"""Data-plane probe implementations that run inside the in-region probe.

Each probe is a pure function of a JobSpec (plus a progress callback) that
issues real invokes to the target endpoint and returns an ObservationBundle.
No adapter state, no cloud SDK — just HTTP to a public endpoint. This is the
code that must run co-located with the runtime-under-test.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .oss_sink import OssChunkSink

import requests

from clousight_bench.core.observation import ObservationBundle
from clousight_bench.core.stats import percentiles
from clousight_bench.domains.agent_runtime import protocol
from clousight_bench.domains.agent_runtime.adapters.base import (
    CancellationResult,
    CapabilityNotSupported,
    CeilingResult,
    HOLResult,
    LoadResult,
    RateLimitResult,
    RetentionResult,
    ScalePoint,
    SoakResult,
    StartupCurveResult,
)
from clousight_bench.domains.agent_runtime.transport_base import auth_headers as _auth_headers

from .invoke import ProbeInvoker
from .jobs import JobProgress, JobSpec

TTFT_WARMUP = 1
TTFT_SAMPLES = 5


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[idx]


ProgressCb = Callable[[JobProgress, dict], None]


def measure_ttft(
    session: requests.Session,
    endpoint_url: str,
    session_header: str,
    session_id: str,
    mock_base_url: str,
    mock_token: str,
) -> float:
    """One streaming invoke; return time-to-first-SSE-data-line in ms.

    Falls back to 0.0 if the endpoint does not return an event-stream (an older
    non-streaming agent), matching the T1.9 fallback contract.
    """
    tool = {"target": "prices", "method": "GET", "params": {"provider": "aliyun"}}
    body = protocol.encode_invoke_stream(
        tool, mock_base_url, mock_token=mock_token or None, session_id=session_id
    )
    url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
    t0 = time.perf_counter()
    resp = session.post(url, json=body, headers={session_header: session_id}, stream=True, timeout=120)
    resp.raise_for_status()
    if "text/event-stream" not in str(resp.headers.get("Content-Type") or ""):
        return 0.0
    for raw_line in resp.iter_lines(decode_unicode=True):
        if raw_line and str(raw_line).startswith("data:"):
            return (time.perf_counter() - t0) * 1000
    return (time.perf_counter() - t0) * 1000


def _measure_ttft_safe(
    session: requests.Session,
    spec: JobSpec,
    session_id: str,
) -> float | None:
    """measure_ttft that swallows transport/HTTP errors → None.

    On-demand AgentRun recycles instances unpredictably, so a warm-path sample
    can hit a 429/connection-reset mid-sweep. Returning None lets the caller
    retry or drop the sample instead of aborting the whole probe.
    """
    try:
        return measure_ttft(
            session,
            spec.target_endpoint,
            spec.session_header_scheme,
            session_id,
            spec.mock_base_url,
            spec.mock_token,
        )
    except Exception:
        return None


def run_ttft(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """Two-dimensional TTFT: cold-start cost + warm steady-state.

    AgentRun code-mode cold-starts a fresh instance in ~86s on EVERY new session
    (proven: a 475-byte empty agent also takes 86s — it's a platform-fixed cost,
    not our agent). A naive per-sample-new-session measurement therefore records
    ~86s every time. Instead we:

    1. **Cold-start dimension:** poll ONE stable session until its first-token
       time drops below ``warm_threshold_ms`` (or attempts exhaust). The first
       poll's latency IS the cold-start cost (``cold_start_ms``).
    2. **Warm steady-state dimension:** keep hitting that SAME warm session and
       collect samples that come back under the warm threshold. Reuse is flaky
       (a sample may hit a recycled/cold instance or a 429), so each sample gets
       a few retries; samples that never warm are dropped, and ``warm_reliable``
       flags whether we got enough clean warm samples to trust the steady-state.

    ``warmup``/``samples`` still come from ``spec.params`` (T1.9); the module
    constants are the fallback defaults.
    """
    samples = int(spec.params.get("samples", TTFT_SAMPLES))
    warm_threshold_ms = float(spec.params.get("warm_threshold_ms", 30000.0))
    max_warm_attempts = int(spec.params.get("max_warm_attempts", 6))
    sample_retries = int(spec.params.get("sample_retries", 3))
    session = requests.Session()
    t_start = time.perf_counter()

    def report(phase: str, completed: int, ms_so_far: list[float]) -> None:
        prog = JobProgress(
            phase=phase, completed=completed, total=samples, elapsed_s=round(time.perf_counter() - t_start, 3)
        )
        metrics = {"last_ttft_ms": ms_so_far[-1]} if ms_so_far else {}
        progress_cb(prog, metrics)

    # One stable session for the whole probe so the warm instance is reused.
    sid = f"ttft-{int(t_start * 1000)}"

    # --- Phase 1: cold-start dimension — warm up the single session ---
    cold_start_ms: float | None = None
    warmed = False
    for a in range(max_warm_attempts):
        ms = _measure_ttft_safe(session, spec, sid)
        if cold_start_ms is None and ms is not None:
            cold_start_ms = round(ms, 3)  # first successful poll = cold-start cost
        report("warmup", a, [ms] if ms is not None else [])
        if ms is not None and ms < warm_threshold_ms:  # 0.0 = non-stream fallback → still warm
            warmed = True
            break

    # --- Phase 2: warm steady-state dimension — reuse the warm session ---
    ttft_ms: list[float] = []
    report("sample", 0, ttft_ms)
    for i in range(samples):
        got: float | None = None
        for _ in range(sample_retries):
            ms = _measure_ttft_safe(session, spec, sid)
            if ms is not None and ms < warm_threshold_ms:  # 0.0 = non-stream fallback → still warm
                got = round(ms, 3)
                break
        if got is not None:
            ttft_ms.append(got)
        report("sample", i + 1, ttft_ms)

    warm_reliable = warmed and len(ttft_ms) >= max(1, int(samples * 0.6))
    return ObservationBundle(
        observations={
            "capability": "supported",
            "ttft_ms": ttft_ms,  # warm steady-state samples (may be short if flaky)
            "cold_start_ms": cold_start_ms,
            "warm_samples": len(ttft_ms),
            "requested_samples": samples,
            "warm_reliable": warm_reliable,
        },
        series={"ttft_ms": [[i + 1, v] for i, v in enumerate(ttft_ms)]},
    )


STARTUP_CURVE_CALLS = 8


def run_startup_curve(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """T1.13 startup-convergence curve: fire ``n_calls`` calls on the same session
    and record each end-to-end latency.

    Characterises the platform's **instance reuse / warm-up behaviour**: the 1st
    call is a cold start (instance built from scratch); do the 2nd/3rd drop off a
    cliff (hitting a reused warm instance), and at which call does it converge to
    steady state? Convergence speed, steady value and reuse reliability vary a lot
    across platforms — a differentiating dimension that answers the question users
    actually care about: "how slow is the Nth call, really?"

    Measures the **full end-to-end invoke time** (not first-byte TTFT): the actual
    time a user waits for the tool call to return. All calls share one session_id
    so the platform has a chance to reuse a warm instance. Derived metrics:

      - ``cold_start_ms``       1st call (cold)
      - ``second_call_ms`` / ``third_call_ms``  2nd/3rd call (points users care about)
      - ``warm_steady_ms``      steady-state median (from the 2nd call on, successful and below threshold)
      - ``speedup_ratio``       cold / warm speedup
      - ``warmed_after_n_calls`` which call first landed in the warm zone
      - ``reuse_reliable``      enough warm samples and zero errors -> reuse is stable
    """
    n_calls = int(spec.params.get("n_calls", STARTUP_CURVE_CALLS))
    warm_threshold_ms = float(spec.params.get("warm_threshold_ms", 30000.0))
    inv = ProbeInvoker(spec)
    base = spec.mock_base_url
    mock_token = spec.mock_token
    sid = inv.create_session()  # single session so the platform can reuse the warm instance
    body = protocol.encode_invoke(
        {"target": "prices", "method": "GET", "params": {"provider": "aliyun"}},
        base,
        mock_token=mock_token or None,
        session_id=sid,
    )
    t_start = time.perf_counter()
    curve: list[dict[str, Any]] = []
    try:
        for i in range(n_calls):
            t0 = time.perf_counter()
            ok = True
            try:
                resp = inv.invoke(sid, body)
                ok = bool(protocol.decode_result(resp).get("ok"))
            except Exception:
                ok = False
            ms = round((time.perf_counter() - t0) * 1000, 2)
            curve.append({"call": i + 1, "ms": ms, "ok": ok})
            progress_cb(
                JobProgress("call", i + 1, n_calls, round(time.perf_counter() - t_start, 3)),
                {"last_ms": ms, "ok": ok},
            )
            if sink is not None:
                sink.append("series", {"call": i + 1, "ms": ms, "ok": ok})
    finally:
        inv.destroy_session(sid)

    r = StartupCurveResult.from_curve([(c["ms"], c["ok"]) for c in curve], warm_threshold_ms)
    return ObservationBundle(
        observations={
            "capability": "supported",
            "curve_ms": r.curve_ms,
            "cold_start_ms": r.cold_start_ms,
            "second_call_ms": r.second_call_ms,
            "third_call_ms": r.third_call_ms,
            "warm_steady_ms": r.warm_steady_ms,
            "speedup_ratio": r.speedup_ratio,
            "warmed_after_n_calls": r.warmed_after_n_calls,
            "reuse_reliable": r.reuse_reliable,
            "errors": r.errors,
            "n_calls": len(curve),
        },
        series={"curve_ms": [[c["call"], c["ms"]] for c in curve]},
    )


def run_sustained_load(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """Real concurrent sustained load: token-bucket + thread pool, measuring true
    throughput and tail latency.

    Worker count = min(target_rps * estimated_latency, 32) (Little's Law).
    Throughput uses actual wall-clock time (after all in-flight requests finish)
    to avoid inflating throughput under high tail latency (requests still
    executing after the deadline are not counted in duration_s but are counted in
    n, which would make n/duration_s artificially high).
    """
    duration_s: float = spec.params.get("duration_s", 60.0)
    target_rps: float = spec.params.get("target_rps", 50.0)

    inv = ProbeInvoker(spec)

    # Warm one session first so the ~86s cold start is absorbed here (reported as
    # cold_start_ms) instead of blowing up the latency estimate that sizes the
    # worker pool. The concurrent workers below intentionally use FRESH sessions —
    # on-demand cold-start-per-request IS the real behaviour under load, so their
    # p50/p99 honestly include it.
    warm_threshold_ms = float(spec.params.get("warm_threshold_ms", 30000.0))
    warm_session = inv.create_session()
    cold_start_ms, warmed = inv.ensure_warm(warm_session, warm_threshold_ms=warm_threshold_ms)
    # Use the warm session to estimate steady-state latency and pick concurrency.
    warm_body = protocol.encode_invoke(
        {"target": "prices", "method": "GET", "params": {"provider": "aliyun"}},
        spec.mock_base_url,
        mock_token=spec.mock_token or None,
        session_id=warm_session,
    )
    _t0 = time.perf_counter()
    try:
        inv.invoke(warm_session, warm_body)
    except Exception:
        pass
    probe_ms = (time.perf_counter() - _t0) * 1000
    inv.destroy_session(warm_session)
    estimated_latency_s = max(probe_ms / 1000, 0.1)
    # concurrency = target_rps x estimated latency (Little's Law), capped at 32
    concurrency = min(max(int(target_rps * estimated_latency_s) + 1, 4), 32)

    progress_cb(JobProgress("probe", 0, 1, 0.0), {})

    latencies: list[float] = []
    errors_count = 0
    transport_errors = 0
    runtime_errors = 0
    tool_errors = 0
    lock = threading.Lock()
    deadline = time.perf_counter() + duration_s

    def _worker() -> None:
        nonlocal errors_count, transport_errors, runtime_errors, tool_errors
        while time.perf_counter() < deadline:
            ok, ms, err_type = inv.one_tool_call_classified()
            with lock:
                latencies.append(ms)
                if not ok:
                    errors_count += 1
                    if err_type == "transport":
                        transport_errors += 1
                    elif err_type == "runtime":
                        runtime_errors += 1
                    else:  # "tool"
                        tool_errors += 1
            if sink is not None:
                sink.append("raw", {"ok": ok, "ms": ms, "err_type": err_type})

    actual_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_worker) for _ in range(concurrency)]
        for f in futures:
            f.result()
    actual_elapsed = time.perf_counter() - actual_start  # includes in-flight tail

    n = len(latencies) or 1
    p = percentiles(latencies)
    # Use actual_elapsed (not duration_s) so long-tail requests don't inflate RPS.
    actual_rps = round(n / actual_elapsed, 3)
    r = LoadResult(
        throughput_rps=actual_rps,
        p50_ms=round(p[50], 2),
        p99_ms=round(p[99], 2),
        jitter_ms=round(p[99] - p[50], 2),
        error_rate=round(errors_count / n, 4),
        requests=n,
        duration_s=round(actual_elapsed, 2),
        transport_error_rate=round(transport_errors / n, 4),
        runtime_error_rate=round(runtime_errors / n, 4),
        tool_error_rate=round(tool_errors / n, 4),
    )

    progress_cb(JobProgress("load", 1, 1, actual_elapsed), {"throughput_rps": actual_rps})

    if sink is not None:
        sink.append("series", {"t": round(actual_elapsed, 3), "throughput_rps": actual_rps})

    return ObservationBundle(
        observations={
            "capability": "supported",
            "throughput_rps": r.throughput_rps,
            "p50_ms": r.p50_ms,
            "p99_ms": r.p99_ms,
            "jitter_ms": r.jitter_ms,
            "error_rate": r.error_rate,
            "transport_error_rate": r.transport_error_rate,
            "runtime_error_rate": r.runtime_error_rate,
            "tool_error_rate": r.tool_error_rate,
            "requests": r.requests,
            "duration_s": r.duration_s,
            "target_rps": target_rps,
            "cold_start_ms": cold_start_ms,
            "warmed": warmed,
        }
    )


def run_soak(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """Sustained-availability probe: loop single calls for duration_s and track the success rate."""
    duration_s: float = spec.params.get("duration_s", 60.0)

    inv = ProbeInvoker(spec)
    warm_threshold_ms = float(spec.params.get("warm_threshold_ms", 30000.0))
    _warm_sid = inv.create_session()
    cold_start_ms, warmed = inv.ensure_warm(_warm_sid, warm_threshold_ms=warm_threshold_ms)
    inv.destroy_session(_warm_sid)
    deadline = time.perf_counter() + duration_s
    req_count, errors = 0, 0
    progress_counter = 0

    while time.perf_counter() < deadline:
        ok, _ = inv.one_tool_call()
        req_count += 1
        progress_counter += 1
        if not ok:
            errors += 1
        if sink is not None:
            sink.append("raw", {"ok": ok, "i": req_count})
        # Report progress every ~50 requests
        if progress_counter >= 50:
            elapsed = time.perf_counter() - (deadline - duration_s)
            n = req_count or 1
            progress_cb(
                JobProgress("soak", req_count, 0, elapsed),
                {"error_rate": errors / n},
            )
            progress_counter = 0

    n = req_count or 1
    r = SoakResult(
        availability=1.0 - errors / n,
        error_rate=errors / n,
        requests=req_count,
        window_s=duration_s,
    )

    return ObservationBundle(
        observations={
            "capability": "supported",
            "availability": r.availability,
            "error_rate": r.error_rate,
            "requests": r.requests,
            "window_s": r.window_s,
            "cold_start_ms": cold_start_ms,
            "warmed": warmed,
        }
    )


# --- idle-retention tiering (T1.5 / T1.14) -----------------------------------
#
# AgentRun/FC idle instances are NOT binary hot/cold — the platform docs describe
# three tiers whose wake cost differs by orders of magnitude:
#   active / shallow hibernation (shallow) — sub-second, ms-level wake, "almost no cold start"
#   deep hibernation (deep)                — seconds to recover
#   instance recycled (recycled/cold)      — full rebuild, ~86s on the next call
# The idle→destroy threshold is configurable via CreateAgentRuntime's
# ``sessionIdleTimeoutSeconds`` but its DEFAULT is deliberately undocumented, so an
# empirical sweep is the only way to learn "how long until it goes
# cold again". These constants classify a wake latency into a tier relative to the
# measured warm baseline; all are overridable per-probe via params.
RETENTION_WARMUP = 5
# Idle points probed FROM warm; each re-invoke wakes the instance so the next
# point is idle-from-warm again. Capped so the total sweep stays ~5min (the probe
# breaks early once it observes a full recycle). Override via wait_intervals_s.
RETENTION_INTERVALS_S = [30, 120, 300]
DEEP_WAKE_FACTOR = 3.0  # wake > factor × warm_p95 ⇒ left shallow hibernation
COLD_WAKE_MS = 15000.0  # wake ≥ this ⇒ instance was recycled (full cold rebuild)


def _retention_body(spec: JobSpec, session_id: str) -> dict[str, Any]:
    """A light, correlation-id-free ``prices`` invoke body for idle probing."""
    return protocol.encode_invoke(
        {"target": "prices", "method": "GET", "params": {"provider": "aliyun"}},
        spec.mock_base_url,
        mock_token=spec.mock_token or None,
        session_id=session_id,
    )


def _timed_invoke(inv: ProbeInvoker, session_id: str, body: dict[str, Any]) -> float:
    """One invoke on ``session_id``; return end-to-end latency in ms (errors → the
    elapsed time, so a slow-then-failing cold wake still reads as slow)."""
    t0 = time.perf_counter()
    try:
        inv.invoke(session_id, body)
    except Exception:  # noqa: BLE001 — latency is the signal, not success
        pass
    return (time.perf_counter() - t0) * 1000


def _classify_wake(
    wake_ms: float,
    warm_p95: float,
    *,
    cold_ms: float = COLD_WAKE_MS,
    deep_factor: float = DEEP_WAKE_FACTOR,
) -> str:
    """Bucket a post-idle wake latency into shallow / deep / cold."""
    if wake_ms >= cold_ms:
        return "cold"
    if wake_ms > warm_p95 * deep_factor:
        return "deep"
    return "shallow"


def run_warm_retention(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """Tiered idle sweep: after warming an instance, idle in steps and re-invoke
    the same session, observing which tier each idle interval drops the instance
    into (shallow hibernation ms / deep hibernation s / recycled full-cold).

    Using ONE stable session (not a fresh one each time) is what actually measures
    "how long this session's instance stays alive". Every re-invoke wakes the
    instance, so each interval is "idle wait_s from warm", mapping idle duration ->
    tier. Break on the first full-cold (recycled) observation (it's dead, no point
    waiting more). Emits shallow_retention_s / deep_onset_s / cold_recycle_s; if the
    longest interval is still not recycled, sweep_capped=True (the real recycle
    window is > the scan cap).
    """
    warmup: int = spec.params.get("warmup_samples", RETENTION_WARMUP)
    intervals: list = spec.params.get("wait_intervals_s", RETENTION_INTERVALS_S)
    cold_ms = float(spec.params.get("cold_wake_ms", COLD_WAKE_MS))
    deep_factor = float(spec.params.get("deep_wake_factor", DEEP_WAKE_FACTOR))

    inv = ProbeInvoker(spec)
    t_start = time.perf_counter()
    sid = inv.create_session()
    body = _retention_body(spec, sid)

    # Warm an instance + collect a baseline distribution; p95 is the tiering
    # threshold. The first sample may be a cold start (~86s if the underlying FC
    # pool is all-cold), reported separately as cold_start_ms; the baseline uses
    # only the later warm samples, so the cold start isn't dragged into the
    # tiering threshold (otherwise warm_p95 is inflated and a real recycle gets
    # misclassified as "still warm").
    warmup_samples: list[float] = []
    for i in range(warmup):
        warmup_samples.append(_timed_invoke(inv, sid, body))
        elapsed = time.perf_counter() - t_start
        progress_cb(JobProgress("warmup", i + 1, warmup, elapsed), {})
    cold_start_ms = round(warmup_samples[0], 2) if warmup_samples else None
    warm_pool = warmup_samples[1:] if len(warmup_samples) > 1 else warmup_samples
    warm_p50 = percentiles(warm_pool)[50]
    warm_p95 = percentiles(warm_pool)[95]

    tiers: list[dict[str, Any]] = []
    shallow_retention_s = 0.0
    deep_onset_s: float | None = None
    cold_recycle_s: float | None = None
    sweep_capped = False
    for idx, wait_s in enumerate(intervals):
        time.sleep(wait_s)
        ms = _timed_invoke(inv, sid, body)
        tier = _classify_wake(ms, warm_p95, cold_ms=cold_ms, deep_factor=deep_factor)
        tiers.append({"idle_s": float(wait_s), "wake_ms": round(ms, 2), "tier": tier})
        elapsed = time.perf_counter() - t_start
        progress_cb(JobProgress("idle-probe", idx + 1, len(intervals), elapsed), {"tier": tier})
        if tier == "shallow":
            shallow_retention_s = float(wait_s)
            if idx == len(intervals) - 1:
                sweep_capped = True  # longest interval still warm: real window > scan cap
        elif tier == "deep":
            if deep_onset_s is None:
                deep_onset_s = float(wait_s)
            if idx == len(intervals) - 1:
                sweep_capped = True  # only reached deep hibernation at the longest interval, not yet recycled
        else:  # cold — instance recycled, no point idling longer
            cold_recycle_s = float(wait_s)
            break
    inv.destroy_session(sid)

    r = RetentionResult(
        retention_ms=shallow_retention_s * 1000.0,  # back-compat: shallow window in ms
        keeps_warm=shallow_retention_s > 0,
    )
    return ObservationBundle(
        observations={
            "capability": "supported",
            "retention_ms": r.retention_ms,
            "keeps_warm": r.keeps_warm,
            "cold_start_ms": cold_start_ms,
            "warm_p50_ms": round(warm_p50, 2),
            "shallow_retention_s": shallow_retention_s,
            "deep_onset_s": deep_onset_s,
            "cold_recycle_s": cold_recycle_s,
            "sweep_capped": sweep_capped,
            "max_idle_s": float(intervals[-1]) if intervals else 0.0,
            "tiers": tiers,
        }
    )


def run_idle_timeout_honor(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """T1.14 — idle-timeout config honor + post-promise decay curve.

    ``sessionIdleTimeoutSeconds`` is a "keep-alive within the promised time"
    contract: set T seconds and the platform should guarantee the **instance
    stays warm for T seconds** (recycling after the promise window is the
    platform's cost freedom, not a breach). So:

      1) Honor check: within the promise window (idle < T, using ~0.8T) re-invoke
         the same session; still warm -> honored. Going cold inside the promise
         window = the platform didn't honor its promise -> honored=False.
      2) Post-promise decay probe: keep re-invoking at 1min / 3min / 5min idle and
         observe at which interval the instance enters deep hibernation
         (second-level wake) or goes cold (recycled, full-cold wake). Break on
         cold, capped at 5min (no point probing longer). This answers "after the
         promise window, how much longer does the instance stay warm for free".
    """
    configured = float(spec.params.get("session_idle_timeout_s", 10.0))
    # In-promise probe point: idle < configured (default 0.8x, at least 2s).
    promise_idle = float(spec.params.get("promise_idle_s", max(2.0, configured * 0.8)))
    # Post-promise decay tiers (seconds): default 1/3/5min, stop on cold, capped at 5min.
    decay_intervals: list = spec.params.get("decay_intervals_s", [60, 180, 300])
    cold_ms = float(spec.params.get("cold_wake_ms", COLD_WAKE_MS))
    deep_factor = float(spec.params.get("deep_wake_factor", DEEP_WAKE_FACTOR))

    inv = ProbeInvoker(spec)
    t_start = time.perf_counter()
    sid = inv.create_session()
    body = _retention_body(spec, sid)

    # First sample may pay cold start (~86s on a cold FC pool); report it
    # separately and base the warm threshold only on the later warm samples.
    warm_samples = [_timed_invoke(inv, sid, body) for _ in range(3)]
    cold_start_ms = round(warm_samples[0], 2)
    warm_pool = warm_samples[1:]
    warm_p50 = percentiles(warm_pool)[50]
    warm_p95 = percentiles(warm_pool)[95]
    progress_cb(JobProgress("warmup", 3, 3, time.perf_counter() - t_start), {})

    # 1) Honor check: within the promise window (< configured) it should still be warm.
    time.sleep(promise_idle)
    promise_ms = _timed_invoke(inv, sid, body)
    promise_tier = _classify_wake(promise_ms, warm_p95, cold_ms=cold_ms, deep_factor=deep_factor)
    honored = promise_tier == "shallow"  # stayed warm within the promised time = honored
    progress_cb(JobProgress("promise", 1, 1, time.perf_counter() - t_start), {"tier": promise_tier})

    # 2) Post-promise decay probe: find the interval of deep hibernation / going cold.
    decay_tiers: list[dict[str, Any]] = []
    deep_onset_s: float | None = None
    cold_onset_s: float | None = None
    decay_capped = False
    for idx, wait_s in enumerate(decay_intervals):
        time.sleep(wait_s)
        ms = _timed_invoke(inv, sid, body)
        tier = _classify_wake(ms, warm_p95, cold_ms=cold_ms, deep_factor=deep_factor)
        decay_tiers.append({"idle_s": float(wait_s), "wake_ms": round(ms, 2), "tier": tier})
        progress_cb(
            JobProgress("decay", idx + 1, len(decay_intervals), time.perf_counter() - t_start),
            {"tier": tier},
        )
        if tier == "deep" and deep_onset_s is None:
            deep_onset_s = float(wait_s)
        if tier == "cold":
            cold_onset_s = float(wait_s)
            break
        if idx == len(decay_intervals) - 1:
            decay_capped = True  # still not cold at the longest interval (may be shallow/deep)

    inv.destroy_session(sid)
    return ObservationBundle(
        observations={
            "capability": "supported",
            "configured_idle_s": configured,
            "promise_idle_s": promise_idle,
            "promise_wake_ms": round(promise_ms, 2),
            "promise_tier": promise_tier,
            "honored": honored,
            "deep_onset_s": deep_onset_s,
            "cold_onset_s": cold_onset_s,
            "decay_capped": decay_capped,
            "decay_tiers": decay_tiers,
            "warm_p50_ms": round(warm_p50, 2),
            "cold_start_ms": cold_start_ms,
        }
    )


def run_rate_limit(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """Stepped concurrency probe for rate limiting: observe the first level to
    return 429.

    Checks the AgentRun data-plane HTTP status directly (bypassing run_tool_plan),
    captures the Retry-After header, and confirms whether the 429 contract is
    complete.
    onset_rps = the smallest concurrency that triggers throttling
    (0 = not triggered within the tested range).
    """
    endpoint_url = spec.target_endpoint
    if not endpoint_url:
        raise RuntimeError("run_rate_limit: empty target_endpoint")

    url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
    mock = spec.mock_base_url
    mock_token = spec.mock_token
    body = protocol.encode_invoke(
        {"target": "prices", "method": "GET"},
        mock,
        mock_token=mock_token or None,
    )
    inv = ProbeInvoker(spec)
    session_obj = inv.session
    warm_threshold_ms = float(spec.params.get("warm_threshold_ms", 30000.0))
    _warm_sid = inv.create_session()
    cold_start_ms, warmed = inv.ensure_warm(_warm_sid, warm_threshold_ms=warm_threshold_ms)
    inv.destroy_session(_warm_sid)
    levels: list = spec.params.get("burst_levels", [10, 20, 40, 80])
    onset_rps = 0.0
    retry_after_ms = 0.0
    honors_429 = False
    t_start = time.perf_counter()

    for idx, burst_n in enumerate(levels):

        def _raw_call(i: int, _n: int = burst_n) -> tuple[int, float]:
            sid = f"rl-{_n}-{i}"
            try:
                resp = session_obj.post(
                    url,
                    json=body,
                    headers={spec.session_header_scheme: sid},
                    timeout=30,
                )
                ra = resp.headers.get("Retry-After", "")
                ra_ms = float(ra) * 1000 if ra else 0.0
                return resp.status_code, ra_ms
            except Exception:
                return 0, 0.0

        with ThreadPoolExecutor(max_workers=burst_n) as pool:
            results = list(pool.map(_raw_call, range(burst_n)))

        elapsed = time.perf_counter() - t_start
        progress_cb(JobProgress("burst", idx + 1, len(levels), elapsed), {"level": burst_n})

        four_twenty_nines = [(s, ra) for s, ra in results if s == 429]
        if four_twenty_nines:
            onset_rps = float(burst_n)
            honors_429 = True
            retry_after_ms = four_twenty_nines[0][1]
            break

    r = RateLimitResult(
        throttle_onset_rps=onset_rps,
        retry_after_ms=retry_after_ms,
        honors_429=honors_429,
    )
    return ObservationBundle(
        observations={
            "capability": "supported",
            "throttle_onset_rps": r.throttle_onset_rps,
            "retry_after_ms": r.retry_after_ms,
            "honors_429": r.honors_429,
            "cold_start_ms": cold_start_ms,
            "warmed": warmed,
        }
    )


def run_concurrency_ceiling(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """Stepped concurrency-ceiling probe: ramp up concurrency to find the level
    where the rejection rate exceeds the threshold."""
    endpoint_url = spec.target_endpoint
    if not endpoint_url:
        raise RuntimeError("run_concurrency_ceiling: empty target_endpoint")

    url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
    mock = spec.mock_base_url
    mock_token = spec.mock_token
    body = protocol.encode_invoke(
        {"target": "prices", "method": "GET"},
        mock,
        mock_token=mock_token or None,
    )
    inv = ProbeInvoker(spec)
    session_obj = inv.session
    warm_threshold_ms = float(spec.params.get("warm_threshold_ms", 30000.0))
    _warm_sid = inv.create_session()
    cold_start_ms, warmed = inv.ensure_warm(_warm_sid, warm_threshold_ms=warm_threshold_ms)
    inv.destroy_session(_warm_sid)
    levels: list = spec.params.get("burst_levels", [50, 100, 200, 500])
    rejection_threshold: float = spec.params.get("rejection_threshold", 0.1)

    ceiling = None
    hard_limit = False
    t_start = time.perf_counter()

    for idx, burst_n in enumerate(levels):

        def _call(i: int, _n: int = burst_n) -> int:
            try:
                resp = session_obj.post(
                    url,
                    json=body,
                    headers={spec.session_header_scheme: f"ceil-{_n}-{i}"},
                    timeout=15,
                )
                return resp.status_code
            except Exception:
                return 0

        with ThreadPoolExecutor(max_workers=burst_n) as pool:
            status_codes = list(pool.map(_call, range(burst_n)))

        elapsed = time.perf_counter() - t_start
        progress_cb(JobProgress("burst", idx + 1, len(levels), elapsed), {"level": burst_n})

        rejections = sum(1 for s in status_codes if s in (429, 503, 0))
        rejection_rate = rejections / burst_n

        if rejection_rate > rejection_threshold:
            ceiling = burst_n
            hard_limit = any(s == 429 for s in status_codes)  # 429 = hard limit
            break

    r = CeilingResult(
        max_in_flight=ceiling if ceiling else levels[-1],
        hard_limit=hard_limit,
    )
    return ObservationBundle(
        observations={
            "capability": "supported",
            "max_in_flight": r.max_in_flight,
            "hard_limit": r.hard_limit,
            "cold_start_ms": cold_start_ms,
            "warmed": warmed,
        }
    )


def run_cancellation(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """Real cancellation probe: force a client disconnect with a very short
    timeout and verify the endpoint recovers from it.

    honored=True: a timeout was raised = client cancellation is effective.
    teardown_ran=True: the endpoint still responds after the timeout (session not corrupted).
    residual: any abnormal state detected after cancellation.
    """
    # CLIENT_TIMEOUT_S is far below any AgentRun data-plane call latency (usually 300ms+).
    CLIENT_TIMEOUT_S: float = spec.params.get("client_timeout_s", 0.1)

    endpoint_url = spec.target_endpoint
    if not endpoint_url:
        raise RuntimeError("run_cancellation: empty target_endpoint")

    inv = ProbeInvoker(spec)
    warm_threshold_ms = float(spec.params.get("warm_threshold_ms", 30000.0))
    _warm_sid = inv.create_session()
    cold_start_ms, warmed = inv.ensure_warm(_warm_sid, warm_threshold_ms=warm_threshold_ms)
    inv.destroy_session(_warm_sid)
    residual: list[str] = []
    honored = False
    teardown_ran = False
    t_start = time.perf_counter()

    try:
        # Step 1: warm up
        ok_warm, _ = inv.one_tool_call()
        if not ok_warm:
            residual.append("warm-up call failed before cancellation probe")

        progress_cb(JobProgress("warmup", 1, 3, time.perf_counter() - t_start), {})

        # Step 2: fire a request with CLIENT_TIMEOUT_S → always triggers Timeout
        session_id = inv.create_session()
        try:
            mock = spec.mock_base_url
            mock_token = spec.mock_token
            body = protocol.encode_invoke(
                {"target": "prices", "method": "GET"},
                mock,
                mock_token=mock_token or None,
            )
            url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
            session_obj = inv.session
            try:
                session_obj.post(
                    url,
                    json=body,
                    headers={spec.session_header_scheme: session_id},
                    timeout=CLIENT_TIMEOUT_S,
                )
                honored = False  # completed within timeout (unexpected)
            except Exception:
                honored = True  # Timeout raised = cancel was honored
        finally:
            inv.destroy_session(session_id)

        progress_cb(JobProgress("cancel", 2, 3, time.perf_counter() - t_start), {})

        # Step 3: verify endpoint is still healthy after the abrupt disconnect
        ok_after, _ = inv.one_tool_call()
        teardown_ran = ok_after
        if not ok_after:
            residual.append("endpoint unhealthy after cancellation: session may be stuck")

        progress_cb(JobProgress("healthcheck", 3, 3, time.perf_counter() - t_start), {})

    except CapabilityNotSupported:
        raise
    except Exception as exc:
        residual.append(f"probe error: {exc!r}")
        teardown_ran = False

    r = CancellationResult(
        honored=honored,
        teardown_ran=teardown_ran,
        residual=residual,
    )
    return ObservationBundle(
        observations={
            "capability": "supported",
            "honored": r.honored,
            "teardown_ran": r.teardown_ran,
            "residual": list(r.residual),
            "cold_start_ms": cold_start_ms,
            "warmed": warmed,
        }
    )


def run_scaling(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """Elasticity probe: run N_REPS at each concurrency level and report the median success_rate and p95_ms.

    The probe has no control credentials and cannot query instance counts;
    observed_instances is always None (ScalePoint.observed_instances: int | None).
    """
    levels: list = spec.params.get("levels", [1, 4, 16, 32, 64])
    N_REPS: int = spec.params.get("n_reps", 3)
    INTER_REP_COOLDOWN_S: float = spec.params.get("inter_rep_cooldown_s", 10)
    inter_level_cooldown_s: float = spec.params.get("inter_level_cooldown_s", 5)

    inv = ProbeInvoker(spec)
    warm_threshold_ms = float(spec.params.get("warm_threshold_ms", 30000.0))
    _warm_sid = inv.create_session()
    cold_start_ms, warmed = inv.ensure_warm(_warm_sid, warm_threshold_ms=warm_threshold_ms)
    inv.destroy_session(_warm_sid)
    base = spec.mock_base_url
    mock_token = spec.mock_token
    body = protocol.encode_invoke(
        {"target": "prices", "method": "GET"},
        base,
        mock_token=mock_token or None,
    )

    # The probe has no control credentials — never call _query_current_instances.
    # observed_instances is always None.

    points: list[ScalePoint] = []
    t_start = time.perf_counter()

    for idx, n in enumerate(levels):
        if n <= 0:
            continue

        rep_success_rates: list[float] = []
        rep_p95s: list[float] = []

        for rep in range(N_REPS):
            if rep > 0:
                time.sleep(INTER_REP_COOLDOWN_S)

            latencies: list[float] = []
            oks = 0

            def _one(_i: int, _n: int = n) -> tuple[bool, float]:
                start = time.perf_counter()
                try:
                    resp = inv.invoke(f"scale-{_n}", body)
                    dt = (time.perf_counter() - start) * 1000
                    return bool(protocol.decode_result(resp).get("ok")), dt
                except Exception:
                    dt = (time.perf_counter() - start) * 1000
                    return False, dt

            with ThreadPoolExecutor(max_workers=n) as pool:
                for ok, dt in pool.map(_one, range(n)):
                    latencies.append(dt)
                    oks += 1 if ok else 0

            rep_success_rates.append(oks / n)
            rep_p95s.append(_p95(latencies))

        # Inter-level cooldown: give OS time to reclaim threads before next level.
        if n != levels[-1]:
            time.sleep(inter_level_cooldown_s)

        # Median across reps: stable even if 1/3 reps is an outlier.
        def _median(vals: list[float]) -> float:
            s = sorted(vals)
            return s[len(s) // 2]

        elapsed = time.perf_counter() - t_start
        progress_cb(JobProgress("level", idx + 1, len(levels), elapsed), {"concurrency": n})

        points.append(
            ScalePoint(
                concurrency=n,
                success_rate=round(_median(rep_success_rates), 4),
                p95_ms=round(_median(rep_p95s), 2),
                observed_instances=None,  # probe has no control creds; never query instances
            )
        )
        if sink is not None:
            sink.append(
                "series",
                {
                    "concurrency": n,
                    "success_rate": round(_median(rep_success_rates), 4),
                    "p95_ms": round(_median(rep_p95s), 2),
                },
            )

    points = sorted(points, key=lambda p: p.concurrency)
    extra = [
        "AgentRun GetAgentRuntime does not expose live instance counts; "
        "elasticity behaviour cannot be observed."
    ]
    return ObservationBundle(
        observations={
            "capability": "supported",
            "points": [
                {
                    "concurrency": p.concurrency,
                    "success_rate": p.success_rate,
                    "p95_ms": p.p95_ms,
                    "observed_instances": p.observed_instances,
                }
                for p in points
            ],
            "instance_visibility_findings": extra,
            "cold_start_ms": cold_start_ms,
            "warmed": warmed,
        },
        series={
            "success_rate": [[p.concurrency, p.success_rate] for p in points],
            "p95_ms": [[p.concurrency, p.p95_ms] for p in points],
        },
    )


def run_hol_blocking(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """T1.12: two-phase HOL blocking probe.

    Phase A (baseline): ``fast_count`` concurrent fast requests (``fast_target``)
        with NO latency injected — establishes the clean p50 baseline.
    Phase B (under-slow): 1 slow request (``slow_target``) with real latency
        injected via POST /latency/config + ``fast_count`` fast requests all on
        the same session. Each request carries a unique correlation_id so the
        mock's per-corr bucket counter doesn't cross-count requests.

    ``serialized`` is True iff fast_p50_under_slow > fast_p50_baseline * 2.0,
    meaning the platform's session queue caused head-of-line blocking.
    ``hol_ratio`` = fast_p50_under_slow / fast_p50_baseline.
    """
    import uuid

    slow_target: str = spec.params.get("slow_target", "reports")
    fast_target: str = spec.params.get("fast_target", "prices")
    fast_count: int = spec.params.get("fast_count", 20)
    slow_latency_ms: int = int(spec.params.get("slow_latency_ms", 500))

    inv = ProbeInvoker(spec)
    base = spec.mock_base_url
    mock_token = spec.mock_token
    latency_url = base.rstrip("/") + "/latency/config"
    t_start = time.perf_counter()

    # ---- Phase A: baseline — fast_count concurrent fast requests, no slow ----
    warm_threshold_ms = float(spec.params.get("warm_threshold_ms", 30000.0))
    session_a = inv.create_session()
    # Warm session_a before the baseline burst so fast_p50_baseline is steady-state
    # (a cold instance would inflate the baseline and mask the HOL ratio).
    cold_start_ms, warmed = inv.ensure_warm(session_a, warm_threshold_ms=warm_threshold_ms)

    def timed_fast(session_id: str, corr: str) -> float:
        body = protocol.encode_invoke(
            {"target": fast_target, "method": "GET"},
            base,
            mock_token=mock_token or None,
            session_id=session_id,
            correlation_id=corr,
        )
        t0 = time.perf_counter()
        try:
            inv.invoke(session_id, body)
        except Exception:
            pass
        return (time.perf_counter() - t0) * 1000

    try:
        with ThreadPoolExecutor(max_workers=fast_count) as pool:
            futs_a = [pool.submit(timed_fast, session_a, uuid.uuid4().hex) for _ in range(fast_count)]
            baseline_latencies = [f.result() for f in futs_a]
    finally:
        inv.destroy_session(session_a)

    fast_p50_baseline = percentiles(baseline_latencies, [50])[50]
    progress_cb(JobProgress("hol_baseline", 1, 2, time.perf_counter() - t_start), {})

    # ---- Phase B: under-slow — inject real latency, 1 slow + fast_count fast ----
    # Configure latency on the mock tool server for the slow target.
    slow_corr = uuid.uuid4().hex
    latency_cfg: dict[str, Any] = {"target": slow_target, "add_ms": slow_latency_ms, "corr": slow_corr}
    try:
        requests.post(
            latency_url, json=latency_cfg, headers=_auth_headers(mock_token), timeout=10
        ).raise_for_status()
    except Exception:
        pass  # best-effort; probe continues even if mock unreachable

    session_b = inv.create_session()
    # Warm session_b too so Phase B measures HOL blocking, not session_b's cold start.
    inv.ensure_warm(session_b, warm_threshold_ms=warm_threshold_ms)

    def timed_slow() -> float:
        body = protocol.encode_invoke(
            {"target": slow_target, "method": "POST"},
            base,
            mock_token=mock_token or None,
            session_id=session_b,
            correlation_id=slow_corr,
        )
        t0 = time.perf_counter()
        try:
            inv.invoke(session_b, body)
        except Exception:
            pass
        return (time.perf_counter() - t0) * 1000

    try:
        with ThreadPoolExecutor(max_workers=1 + fast_count) as pool:
            slow_fut = pool.submit(timed_slow)
            fast_futs_b = [pool.submit(timed_fast, session_b, uuid.uuid4().hex) for _ in range(fast_count)]
            slow_fut.result()
            under_slow_latencies = [f.result() for f in fast_futs_b]
    finally:
        inv.destroy_session(session_b)
        # Clear latency config so it doesn't affect other probes.
        try:
            requests.post(latency_url, json={}, headers=_auth_headers(mock_token), timeout=5)
        except Exception:
            pass

    fast_p50_under_slow = percentiles(under_slow_latencies, [50])[50]
    hol_ratio = round(fast_p50_under_slow / fast_p50_baseline, 4) if fast_p50_baseline > 0 else 0.0
    serialized = fast_p50_under_slow > fast_p50_baseline * 2.0

    elapsed = time.perf_counter() - t_start
    progress_cb(JobProgress("hol_under_slow", 2, 2, elapsed), {"serialized": serialized})

    r = HOLResult(
        serialized=serialized,
        fast_p50_baseline=fast_p50_baseline,
        fast_p50_under_slow=fast_p50_under_slow,
        hol_ratio=hol_ratio,
    )

    return ObservationBundle(
        observations={
            "capability": "supported",
            "fast_p50_baseline": r.fast_p50_baseline,
            "fast_p50_under_slow": r.fast_p50_under_slow,
            "hol_ratio": r.hol_ratio,
            "serialized": r.serialized,
            "cold_start_ms": cold_start_ms,
            "warmed": warmed,
        }
    )


def run_fault_recovery(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """T1.3 platform-visible fault injection + agent retry observation.

    Uses a per-correlation mock bucket to isolate this probe's call counts from
    concurrent traffic, then issues a SINGLE invoke and lets the deployed agent
    retry internally (per its lc_agent 5xx-retry-2 contract). Reads the mock
    call counter afterwards to determine how many times the agent hit the tool.

    Three-state outcome:
      recovered=True,  observed_attempts=3, platform_terminated=False
          → platform let the agent retry until success (healthy signal)
      recovered=False, observed_attempts=3, platform_terminated=False
          → platform let the agent retry but the tool stayed broken (agent exhausted)
      platform_terminated=True
          → the platform killed the invoke during the recovery window (timeout)
    """
    import uuid

    base = spec.mock_base_url
    mock_token = spec.mock_token
    corr = uuid.uuid4().hex  # unique correlation id for this probe run

    # Step 1: Configure fault on mock server — fail only call #1 in this corr bucket.
    fault_config: dict[str, Any] = {"target": "prices", "fail_on_calls": [1], "status": 500, "corr": corr}
    fault_url = base.rstrip("/") + "/fault/config"
    inv = ProbeInvoker(spec)
    session = inv.create_session()
    t_start = time.perf_counter()

    # Warm the session BEFORE injecting the fault so recovery_ms reflects the
    # steady-state recovery window, not the ~86s cold start. The warm-up traffic
    # carries no correlation id, so it never lands in this probe's fault corr
    # bucket. cold_start_ms is reported separately.
    warm_threshold_ms = float(spec.params.get("warm_threshold_ms", 30000.0))
    cold_start_ms, warmed = inv.ensure_warm(session, warm_threshold_ms=warm_threshold_ms)

    try:
        import requests as _requests

        _requests.post(
            fault_url, json=fault_config, headers=_auth_headers(mock_token), timeout=10
        ).raise_for_status()
    except Exception:
        # If mock is unreachable, best-effort — probe will still proceed.
        pass

    progress_cb(JobProgress("configure", 1, 3, time.perf_counter() - t_start), {})

    # Step 2: Issue a single invoke with this correlation id.
    # The agent internally retries 5xx up to 2 times (3 total attempts).
    tool = {"target": "prices", "method": "GET", "params": {"provider": "aliyun"}}
    body = protocol.encode_invoke(
        tool,
        base,
        mock_token=mock_token or None,
        session_id=session,
        correlation_id=corr,
    )

    recovered = False
    platform_terminated = False
    t_invoke = time.perf_counter()
    try:
        resp = inv.invoke(session, body)
        result = protocol.decode_result(resp)
        recovered = bool(result.get("ok"))
    except Exception as exc:
        err_str = str(exc).lower()
        if any(k in err_str for k in ("timeout", "connection", "ssl", "eof", "connect")):
            platform_terminated = True
        # recovered stays False
    finally:
        inv.destroy_session(session)

    # Warm-path recovery window: time of the single fault-injected invoke only
    # (the ~86s cold start was absorbed by ensure_warm and is in cold_start_ms).
    recovery_ms = round((time.perf_counter() - t_invoke) * 1000, 2)

    progress_cb(JobProgress("invoke", 2, 3, time.perf_counter() - t_start), {})

    # Step 3: Read mock server call counter for this corr bucket.
    observed_attempts = 0
    try:
        import requests as _requests

        state_resp = _requests.get(
            base.rstrip("/") + "/fault/state", headers=_auth_headers(mock_token), timeout=10
        )
        state_resp.raise_for_status()
        counts = state_resp.json().get("call_counts", {})
        observed_attempts = int(counts.get(f"prices|{corr}", 0))
    except Exception:
        pass  # can't read counter; observed_attempts stays 0

    progress_cb(JobProgress("observe", 3, 3, time.perf_counter() - t_start), {})

    return ObservationBundle(
        observations={
            "capability": "supported",
            "recovered": recovered,
            "observed_attempts": observed_attempts,
            "recovery_ms": recovery_ms,
            "platform_terminated": platform_terminated,
            "cold_start_ms": cold_start_ms,
            "warmed": warmed,
        }
    )


def run_retry_storm(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """T1.10: mock-counted total attempts + storm-bounded-by attribution.

    Uses a per-correlation mock bucket to isolate this probe's call counts from
    concurrent traffic. Configures the mock server to fail ALL calls (fail_from_call=1,
    fail_count=999) on this corr bucket, then issues a SINGLE invoke and lets the
    deployed agent retry internally (per its lc_agent 5xx-retry-2 contract).
    Reads the mock call counter afterwards to determine how many times the agent
    actually hit the tool.

    Attribution rules:
      total_attempts <= 3 and no timeout → storm_bounded_by = "agent"
          (agent retry contract held — bounded)
      invoke raised Timeout               → storm_bounded_by = "platform"
          (platform cut the invoke before agent could exhaust retries)
      total_attempts > 3                  → storm_bounded_by = "none"
          (anomaly — retry leaked past the agent contract, dangerous)
    """
    import uuid

    base = spec.mock_base_url
    mock_token = spec.mock_token
    corr = uuid.uuid4().hex  # unique correlation id for this probe run

    # Step 1: Configure fault on mock server — fail ALL calls in this corr bucket.
    # "target" is REQUIRED — the mock only applies a fault whose target matches the
    # request's tool target (mock_tools.fault_status_for). Omitting it silently
    # injects nothing, so the storm never forms and total_attempts collapses to 1.
    fault_config: dict[str, Any] = {
        "target": "prices",
        "fail_from_call": 1,
        "fail_count": 999,
        "corr": corr,
    }
    fault_url = base.rstrip("/") + "/fault/config"
    inv = ProbeInvoker(spec)
    session = inv.create_session()
    t_start = time.perf_counter()

    # Warm the session BEFORE injecting the fault so duration_ms reflects the
    # steady-state storm window, not the ~86s cold start. Warm-up traffic carries
    # no correlation id, so it stays out of this probe's fault corr bucket.
    warm_threshold_ms = float(spec.params.get("warm_threshold_ms", 30000.0))
    cold_start_ms, warmed = inv.ensure_warm(session, warm_threshold_ms=warm_threshold_ms)

    try:
        import requests as _requests

        _requests.post(
            fault_url, json=fault_config, headers=_auth_headers(mock_token), timeout=10
        ).raise_for_status()
    except Exception:
        # If mock is unreachable, best-effort — probe will still proceed.
        pass

    progress_cb(JobProgress("configure", 1, 3, time.perf_counter() - t_start), {})

    # Step 2: Issue a single invoke with this correlation id bounded by max_window_s.
    # The agent internally retries 5xx up to 2 times (3 total attempts).
    tool = {"target": "prices", "method": "GET", "params": {"provider": "aliyun"}}
    body = protocol.encode_invoke(
        tool,
        base,
        mock_token=mock_token or None,
        session_id=session,
        correlation_id=corr,
    )

    storm_bounded_by = "agent"  # default; may be overridden on Timeout
    t_invoke = time.perf_counter()
    try:
        inv.invoke(session, body)
        # Invoke completed (agent exhausted retries or succeeded)
    except requests.exceptions.Timeout:
        storm_bounded_by = "platform"
    except Exception:
        raise
    finally:
        inv.destroy_session(session)

    # Warm-path storm window: the single retry-storm invoke only (the ~86s cold
    # start was absorbed by ensure_warm and is reported in cold_start_ms).
    duration_ms = round((time.perf_counter() - t_invoke) * 1000, 2)

    progress_cb(JobProgress("invoke", 2, 3, time.perf_counter() - t_start), {})

    # Step 3: Read mock server call counter for this corr bucket.
    total_attempts = 0
    try:
        import requests as _requests

        state_resp = _requests.get(
            base.rstrip("/") + "/fault/state", headers=_auth_headers(mock_token), timeout=10
        )
        state_resp.raise_for_status()
        counts = state_resp.json().get("call_counts", {})
        total_attempts = int(counts.get(f"prices|{corr}", 0))
    except Exception:
        pass  # can't read counter; total_attempts stays 0

    progress_cb(JobProgress("observe", 3, 3, time.perf_counter() - t_start), {})

    # Derive storm_bounded_by from total_attempts (unless already set to "platform").
    if storm_bounded_by != "platform":
        if total_attempts > 3:
            storm_bounded_by = "none"
        else:
            storm_bounded_by = "agent"

    return ObservationBundle(
        observations={
            "capability": "supported",
            "total_attempts": total_attempts,
            "storm_bounded_by": storm_bounded_by,
            "duration_ms": duration_ms,
            "cold_start_ms": cold_start_ms,
            "warmed": warmed,
        }
    )
