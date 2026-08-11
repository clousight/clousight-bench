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
from typing import TYPE_CHECKING

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
)

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


def run_ttft(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """Warm up, then measure ``samples`` streaming invokes.

    ``warmup``/``samples`` come from ``spec.params`` (T1.9 passes {"warmup", "samples"});
    the module constants are the fallback defaults.
    """
    warmup = int(spec.params.get("warmup", TTFT_WARMUP))
    samples = int(spec.params.get("samples", TTFT_SAMPLES))
    session = requests.Session()
    t_start = time.perf_counter()

    def report(phase: str, completed: int, ms_so_far: list[float]) -> None:
        prog = JobProgress(
            phase=phase, completed=completed, total=samples, elapsed_s=round(time.perf_counter() - t_start, 3)
        )
        metrics = {"last_ttft_ms": ms_so_far[-1]} if ms_so_far else {}
        progress_cb(prog, metrics)

    sid = f"ttft-{int(t_start * 1000)}"
    for _ in range(warmup):
        measure_ttft(
            session,
            spec.target_endpoint,
            spec.session_header_scheme,
            sid,
            spec.mock_base_url,
            spec.mock_token,
        )
    ttft_ms: list[float] = []
    report("sample", 0, ttft_ms)
    for i in range(samples):
        ms = measure_ttft(
            session,
            spec.target_endpoint,
            spec.session_header_scheme,
            f"{sid}-{i}",
            spec.mock_base_url,
            spec.mock_token,
        )
        ttft_ms.append(round(ms, 3))
        report("sample", i + 1, ttft_ms)
    return ObservationBundle(
        observations={"capability": "supported", "ttft_ms": ttft_ms},
        series={"ttft_ms": [[i + 1, v] for i, v in enumerate(ttft_ms)]},
    )


def run_sustained_load(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """真并发持续负载：用令牌桶 + 线程池驱动，真实测量吞吐和尾延迟。

    工作者数量 = min(target_rps * 预估延迟, 32)（Little's Law）。
    吞吐量分母使用实际挂钟时间（含所有 in-flight 请求完成后），避免高尾延迟
    场景下人为高估吞吐量（deadline 后仍在执行的请求不被计入 duration_s 但
    被计入 n，导致 n/duration_s 虚高）。
    """
    duration_s: float = spec.params.get("duration_s", 60.0)
    target_rps: float = spec.params.get("target_rps", 50.0)

    inv = ProbeInvoker(spec)

    # 先做一次探测请求，估计平均延迟，决定并发度
    _, probe_ms = inv.one_tool_call()
    estimated_latency_s = max(probe_ms / 1000, 0.1)
    # 并发度 = target_rps × 估计延迟（Little's Law），上限 32
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
        }
    )


def run_soak(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """持续可用性探测：在 duration_s 内循环单次调用，统计成功率。"""
    duration_s: float = spec.params.get("duration_s", 60.0)

    inv = ProbeInvoker(spec)
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
        }
    )


def run_warm_retention(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """多点检测：建立热实例后，依次等待，观察哪个时间点变冷。

    阈值策略：取 warmup_samples 次热调用的 p95，乘以 2 作为"仍然热"的上限。
    retention_ms = 最后一次仍然热的等待时间（0 = 完全不保活）。
    """
    warmup: int = spec.params.get("warmup_samples", 5)
    intervals: list = spec.params.get("wait_intervals_s", [10, 30, 60])

    inv = ProbeInvoker(spec)
    t_start = time.perf_counter()

    # 建立热实例 + 采集基准分布，用 p95×2 作为阈值
    warmup_samples: list[float] = []
    for i in range(warmup):
        _, ms = inv.one_tool_call()
        warmup_samples.append(ms)
        elapsed = time.perf_counter() - t_start
        progress_cb(JobProgress("warmup", i + 1, warmup, elapsed), {})
    warm_p95 = percentiles(warmup_samples)[95]
    warm_threshold = warm_p95 * 2  # cold start 通常 5-20× warm；2× 保守但足够区分

    last_warm_ms = 0.0
    keeps_warm = False
    for idx, wait_s in enumerate(intervals):
        time.sleep(wait_s)
        _, ms = inv.one_tool_call()
        elapsed = time.perf_counter() - t_start
        if ms <= warm_threshold:
            last_warm_ms = wait_s * 1000.0
            keeps_warm = True
        progress_cb(
            JobProgress("idle-probe", idx + 1, len(intervals), elapsed),
            {"keeps_warm": keeps_warm},
        )
        if ms > warm_threshold:
            break  # 变冷，记录最后一次热点

    r = RetentionResult(
        retention_ms=last_warm_ms,
        keeps_warm=keeps_warm,
    )

    return ObservationBundle(
        observations={
            "capability": "supported",
            "retention_ms": r.retention_ms,
            "keeps_warm": r.keeps_warm,
        }
    )


def run_rate_limit(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """阶梯式并发探测限流：观察首个出现 429 的级别。

    直接检查 AgentRun 数据面的 HTTP 状态（不经过 run_tool_plan），捕获
    Retry-After 头，确认 429 合约是否完整。
    onset_rps = 触发限流的最小并发数（0 = 在测试范围内未触发）。
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
        }
    )


def run_concurrency_ceiling(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """阶梯式并发上限探测：逐步提升并发量，找到拒绝率超阈值的级别。"""
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
        }
    )


def run_cancellation(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """真实取消探测：用极短超时强制客户端断开，验证端点能从中恢复。

    honored=True: 超时异常已抛出 = 客户端取消有效
    teardown_ran=True: 超时后端点仍可正常响应（session 未损坏）
    residual: 取消后检测到的异常状态
    """
    # CLIENT_TIMEOUT_S 远低于任何 AgentRun 数据面调用的实际延迟（通常 300ms+）
    CLIENT_TIMEOUT_S: float = spec.params.get("client_timeout_s", 0.1)

    endpoint_url = spec.target_endpoint
    if not endpoint_url:
        raise RuntimeError("run_cancellation: empty target_endpoint")

    inv = ProbeInvoker(spec)
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
        }
    )


def run_scaling(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """弹性探测：在各并发级别跑 N_REPS 次，报告中位 success_rate 和 p95_ms。

    The probe has no control credentials and cannot query instance counts;
    observed_instances is always None (ScalePoint.observed_instances: int | None).
    """
    levels: list = spec.params.get("levels", [1, 4, 16, 32, 64])
    N_REPS: int = spec.params.get("n_reps", 3)
    INTER_REP_COOLDOWN_S: float = spec.params.get("inter_rep_cooldown_s", 10)
    inter_level_cooldown_s: float = spec.params.get("inter_level_cooldown_s", 5)

    inv = ProbeInvoker(spec)
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
    extra = ["AgentRun GetAgentRuntime 不暴露实时实例数，无法观测弹性行为。"]
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
        },
        series={
            "success_rate": [[p.concurrency, p.success_rate] for p in points],
            "p95_ms": [[p.concurrency, p.p95_ms] for p in points],
        },
    )


def run_hol_blocking(
    spec: JobSpec, progress_cb: ProgressCb, *, sink: OssChunkSink | None = None
) -> ObservationBundle:
    """T1.12: 1 slow + fast_count fast concurrent requests on the same session.

    Fires requests to the ``slow_target`` endpoint (slow) and ``fast_target``
    endpoint (fast) concurrently via the data plane. If the fast requests'
    p99 exceeds half the slow request's latency, HOL blocking is present in
    the session dispatch queue.
    """
    slow_target: str = spec.params.get("slow_target", "reports")
    fast_target: str = spec.params.get("fast_target", "prices")
    fast_count: int = spec.params.get("fast_count", 5)

    inv = ProbeInvoker(spec)
    base = spec.mock_base_url
    mock_token = spec.mock_token
    session_id = inv.create_session()

    def timed_invoke(target: str) -> float:
        body = protocol.encode_invoke(
            {"target": target, "method": "GET"},
            base,
            mock_token=mock_token or None,
        )
        t0 = time.perf_counter()
        try:
            inv.invoke(session_id, body)
        except Exception:
            pass
        return (time.perf_counter() - t0) * 1000

    t_start = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=1 + fast_count) as pool:
            slow_fut = pool.submit(timed_invoke, slow_target)
            fast_futs = [pool.submit(timed_invoke, fast_target) for _ in range(fast_count)]
            slow_ms = slow_fut.result()
            fast_latencies = sorted(f.result() for f in fast_futs)
    finally:
        inv.destroy_session(session_id)

    fast_p50_ms = round(fast_latencies[len(fast_latencies) // 2], 2)
    fast_p99_ms = round(fast_latencies[-1], 2)
    slow_p50_ms = round(slow_ms, 2)
    hol_ratio = round(fast_p99_ms / slow_p50_ms, 4) if slow_p50_ms > 0 else 0.0
    blocked = hol_ratio > 0.5

    elapsed = time.perf_counter() - t_start

    r = HOLResult(
        blocked=blocked,
        fast_p50_ms=fast_p50_ms,
        slow_p50_ms=slow_p50_ms,
        hol_ratio=hol_ratio,
    )

    progress_cb(JobProgress("hol", 1, 1, elapsed), {"blocked": r.blocked})

    return ObservationBundle(
        observations={
            "blocked": r.blocked,
            "fast_p50_ms": r.fast_p50_ms,
            "slow_p50_ms": r.slow_p50_ms,
            "hol_ratio": r.hol_ratio,
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
    fault_config = {"target": "prices", "fail_on_calls": [1], "status": 500, "corr": corr}
    fault_url = base.rstrip("/") + "/fault/config"
    inv = ProbeInvoker(spec)
    session = inv.create_session()
    t_start = time.perf_counter()

    try:
        import requests as _requests

        _requests.post(fault_url, json=fault_config, timeout=10).raise_for_status()
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

    recovery_ms = round((time.perf_counter() - t_start) * 1000, 2)

    progress_cb(JobProgress("invoke", 2, 3, time.perf_counter() - t_start), {})

    # Step 3: Read mock server call counter for this corr bucket.
    observed_attempts = 0
    try:
        import requests as _requests

        state_resp = _requests.get(base.rstrip("/") + "/fault/state", timeout=10)
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
    fault_config = {"fail_from_call": 1, "fail_count": 999, "corr": corr}
    fault_url = base.rstrip("/") + "/fault/config"
    inv = ProbeInvoker(spec)
    session = inv.create_session()
    t_start = time.perf_counter()

    try:
        import requests as _requests

        _requests.post(fault_url, json=fault_config, timeout=10).raise_for_status()
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
    try:
        inv.invoke(session, body)
        # Invoke completed (agent exhausted retries or succeeded)
    except Exception as exc:
        err_str = str(exc).lower()
        if any(k in err_str for k in ("timeout", "connection", "ssl", "eof", "connect")):
            storm_bounded_by = "platform"
        # On any exception: platform bounded the storm
    finally:
        inv.destroy_session(session)

    duration_ms = round((time.perf_counter() - t_start) * 1000, 2)

    progress_cb(JobProgress("invoke", 2, 3, time.perf_counter() - t_start), {})

    # Step 3: Read mock server call counter for this corr bucket.
    total_attempts = 0
    try:
        import requests as _requests

        state_resp = _requests.get(base.rstrip("/") + "/fault/state", timeout=10)
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
        }
    )
