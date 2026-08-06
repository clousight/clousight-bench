"""One dispatch seam for all data-plane probes.

Each packer is a task's execute() body, relocated so the adapter — not the task
— owns the call+pack. This is the cut line the probe-sink needs: a real adapter
can override run_data_plane_probe to send the whole measurement to an in-region
probe, while local-sim keeps flowing through these packers unchanged.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime.adapters.base import (
    AgentRuntimeAdapter,
    CapabilityNotSupported,
)

Packer = Callable[[AgentRuntimeAdapter, dict[str, Any]], ObservationBundle]


def _pack_sustained_load(adapter: AgentRuntimeAdapter, params: dict[str, Any]) -> ObservationBundle:
    duration_s = float(params.get("duration_s", 60.0))
    target_rps = float(params.get("target_rps", 50.0))
    try:
        r = adapter.probe_sustained_load(duration_s, target_rps)
    except CapabilityNotSupported as exc:
        return ObservationBundle(observations={"capability": "unsupported", "reason": str(exc)})
    return ObservationBundle(
        observations={
            "capability": "supported",
            "throughput_rps": r.throughput_rps,
            "p50_ms": r.p50_ms,
            "p99_ms": r.p99_ms,
            "jitter_ms": r.jitter_ms,
            "error_rate": r.error_rate,
            "transport_error_rate": getattr(r, "transport_error_rate", 0.0),
            "runtime_error_rate": getattr(r, "runtime_error_rate", 0.0),
            "tool_error_rate": getattr(r, "tool_error_rate", 0.0),
            "requests": r.requests,
            "duration_s": r.duration_s,
            "target_rps": target_rps,
        }
    )


def _pack_warm_retention(adapter: AgentRuntimeAdapter, params: dict[str, Any]) -> ObservationBundle:
    try:
        r = adapter.probe_warm_retention()
    except CapabilityNotSupported as exc:
        return ObservationBundle(observations={"capability": "unsupported", "reason": str(exc)})
    return ObservationBundle(
        observations={
            "capability": "supported",
            "retention_ms": r.retention_ms,
            "keeps_warm": r.keeps_warm,
        }
    )


def _pack_soak(adapter: AgentRuntimeAdapter, params: dict[str, Any]) -> ObservationBundle:
    duration_s = float(params.get("duration_s", 60.0))
    try:
        r = adapter.probe_soak(duration_s)
    except CapabilityNotSupported as exc:
        return ObservationBundle(observations={"capability": "unsupported", "reason": str(exc)})
    return ObservationBundle(
        observations={
            "capability": "supported",
            "availability": r.availability,
            "error_rate": r.error_rate,
            "requests": r.requests,
            "window_s": r.window_s,
        }
    )


def _pack_rate_limit(adapter: AgentRuntimeAdapter, params: dict[str, Any]) -> ObservationBundle:
    try:
        r = adapter.probe_rate_limit()
    except CapabilityNotSupported as exc:
        return ObservationBundle(observations={"capability": "unsupported", "reason": str(exc)})
    return ObservationBundle(
        observations={
            "capability": "supported",
            "throttle_onset_rps": r.throttle_onset_rps,
            "retry_after_ms": r.retry_after_ms,
            "honors_429": r.honors_429,
        }
    )


def _pack_cancellation(adapter: AgentRuntimeAdapter, params: dict[str, Any]) -> ObservationBundle:
    try:
        r = adapter.probe_cancellation()
    except CapabilityNotSupported as exc:
        return ObservationBundle(observations={"capability": "unsupported", "reason": str(exc)})
    return ObservationBundle(
        observations={
            "capability": "supported",
            "honored": r.honored,
            "teardown_ran": r.teardown_ran,
            "residual": list(r.residual),
        }
    )


def _pack_ttft(adapter: AgentRuntimeAdapter, params: dict[str, Any]) -> ObservationBundle:
    warmup = int(params.get("warmup", 1))
    samples = int(params.get("samples", 5))
    # Warm-up: ensure the runtime is hot before measuring.
    for _ in range(warmup):
        try:
            adapter.probe_ttft()
        except CapabilityNotSupported as exc:
            return ObservationBundle(
                observations={"capability": "unsupported", "reason": str(exc)}
            )
    ttft_ms: list[float] = []
    for _ in range(samples):
        try:
            ms = adapter.probe_ttft()
            ttft_ms.append(ms)
        except CapabilityNotSupported as exc:
            return ObservationBundle(
                observations={"capability": "unsupported", "reason": str(exc)}
            )
    return ObservationBundle(
        observations={"capability": "supported", "ttft_ms": ttft_ms},
        series={"ttft_ms": [[i + 1, v] for i, v in enumerate(ttft_ms)]},
    )


def _pack_concurrency_ceiling(adapter: AgentRuntimeAdapter, params: dict[str, Any]) -> ObservationBundle:
    try:
        r = adapter.probe_concurrency_ceiling()
    except CapabilityNotSupported as exc:
        return ObservationBundle(observations={"capability": "unsupported", "reason": str(exc)})
    return ObservationBundle(
        observations={
            "capability": "supported",
            "max_in_flight": r.max_in_flight,
            "hard_limit": r.hard_limit,
        }
    )


def _pack_scaling(adapter: AgentRuntimeAdapter, params: dict[str, Any]) -> ObservationBundle:
    levels = list(params.get("levels", [1, 4, 16, 32, 64]))
    try:
        points = adapter.probe_scaling(levels)
    except CapabilityNotSupported as exc:
        return ObservationBundle(observations={"capability": "unsupported", "reason": str(exc)})
    points = sorted(points, key=lambda p: p.concurrency)
    all_instances_none = all(
        getattr(p, "observed_instances", None) is None for p in points
    )
    extra_findings: list[str] = []
    if all_instances_none:
        extra_findings.append(
            "AgentRun GetAgentRuntime 不暴露实时实例数，无法观测弹性行为。"
        )
    return ObservationBundle(
        observations={
            "capability": "supported",
            "points": [
                {"concurrency": p.concurrency, "success_rate": p.success_rate,
                 "p95_ms": p.p95_ms,
                 "observed_instances": getattr(p, "observed_instances", None)}
                for p in points
            ],
            **({"instance_visibility_findings": extra_findings} if extra_findings else {}),
        },
        series={
            "success_rate": [[p.concurrency, p.success_rate] for p in points],
            "p95_ms": [[p.concurrency, p.p95_ms] for p in points],
        },
    )


def _pack_fault_recovery(adapter: AgentRuntimeAdapter, params: dict[str, Any]) -> ObservationBundle:
    fault_call_index = int(params.get("fault_call_index", 3))
    # Fault description (matches FAULT in t1_3_fault_recovery.py)
    fault = {"target": "prices", "fail_on_calls": [fault_call_index], "status": 500}
    # Does NOT catch CapabilityNotSupported — re-raises
    trace = adapter.probe_fault_recovery(fault_call_index=fault_call_index)
    return ObservationBundle(
        observations={
            "fault": dict(fault),
            "plan_calls": fault_call_index + 2,
            "completed": trace.completed,
            "final_state": trace.final_state,
            "attempts": [asdict(a) for a in trace.attempts],
        }
    )


def _pack_retry_storm(adapter: AgentRuntimeAdapter, params: dict[str, Any]) -> ObservationBundle:
    max_window_s = float(params.get("max_window_s", 30.0))
    # Does NOT catch CapabilityNotSupported — re-raises
    result = adapter.probe_retry_storm(max_window_s=max_window_s)
    return ObservationBundle(
        observations={
            "storm_behavior": result.storm_behavior,
            "calls_attempted": result.calls_attempted,
            "duration_ms": result.duration_ms,
        }
    )


def _pack_hol_blocking(adapter: AgentRuntimeAdapter, params: dict[str, Any]) -> ObservationBundle:
    # Does NOT catch CapabilityNotSupported — re-raises
    result = adapter.probe_hol_blocking()
    return ObservationBundle(
        observations={
            "blocked": result.blocked,
            "fast_p50_ms": result.fast_p50_ms,
            "slow_p50_ms": result.slow_p50_ms,
            "hol_ratio": result.hol_ratio,
        }
    )


DATA_PLANE_PACKERS: dict[str, Packer] = {
    "sustained_load": _pack_sustained_load,
    "warm_retention": _pack_warm_retention,
    "soak": _pack_soak,
    "rate_limit": _pack_rate_limit,
    "cancellation": _pack_cancellation,
    "ttft": _pack_ttft,
    "concurrency_ceiling": _pack_concurrency_ceiling,
    "scaling": _pack_scaling,
    "fault_recovery": _pack_fault_recovery,
    "retry_storm": _pack_retry_storm,
    "hol_blocking": _pack_hol_blocking,
}


def run_data_plane_probe(
    adapter: AgentRuntimeAdapter,
    name: str,
    params: dict[str, Any] | None = None,
) -> ObservationBundle:
    packer = DATA_PLANE_PACKERS.get(name)
    if packer is None:
        raise ValueError(
            f"unknown data-plane probe {name!r}; "
            f"known: {sorted(DATA_PLANE_PACKERS)}"
        )
    return packer(adapter, params or {})
