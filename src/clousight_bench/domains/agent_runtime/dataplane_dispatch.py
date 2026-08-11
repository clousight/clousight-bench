"""One dispatch seam for all data-plane probes.

Each packer is a task's execute() body, relocated so the adapter — not the task
— owns the call+pack. This is the cut line the probe-sink needs: a real adapter
can override run_data_plane_probe to send the whole measurement to an in-region
probe, while local-sim keeps flowing through these packers unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime.adapters.base import (
    AgentRuntimeAdapter,
    CapabilityNotSupported,
    FaultRecoveryResult,
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
            return ObservationBundle(observations={"capability": "unsupported", "reason": str(exc)})
    ttft_ms: list[float] = []
    for _ in range(samples):
        try:
            ms = adapter.probe_ttft()
            ttft_ms.append(ms)
        except CapabilityNotSupported as exc:
            return ObservationBundle(observations={"capability": "unsupported", "reason": str(exc)})
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
    all_instances_none = all(getattr(p, "observed_instances", None) is None for p in points)
    extra_findings: list[str] = []
    if all_instances_none:
        extra_findings.append("AgentRun GetAgentRuntime 不暴露实时实例数，无法观测弹性行为。")
    return ObservationBundle(
        observations={
            "capability": "supported",
            "points": [
                {
                    "concurrency": p.concurrency,
                    "success_rate": p.success_rate,
                    "p95_ms": p.p95_ms,
                    "observed_instances": getattr(p, "observed_instances", None),
                }
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
    # Does NOT catch CapabilityNotSupported — re-raises
    result: FaultRecoveryResult = adapter.probe_fault_recovery()
    return ObservationBundle(
        observations={
            "capability": "supported",
            "recovered": result.recovered,
            "observed_attempts": result.observed_attempts,
            "recovery_ms": result.recovery_ms,
            "platform_terminated": result.platform_terminated,
        }
    )


def _pack_retry_storm(adapter: AgentRuntimeAdapter, params: dict[str, Any]) -> ObservationBundle:
    max_window_s = float(params.get("max_window_s", 30.0))
    # Does NOT catch CapabilityNotSupported — re-raises
    result = adapter.probe_retry_storm(max_window_s=max_window_s)
    return ObservationBundle(
        observations={
            "capability": result.capability,
            "total_attempts": result.total_attempts,
            "storm_bounded_by": result.storm_bounded_by,
            "duration_ms": result.duration_ms,
        }
    )


def _pack_hol_blocking(adapter: AgentRuntimeAdapter, params: dict[str, Any]) -> ObservationBundle:
    # Does NOT catch CapabilityNotSupported — re-raises
    result = adapter.probe_hol_blocking()
    return ObservationBundle(
        observations={
            "capability": "supported",
            "fast_p50_baseline": result.fast_p50_baseline,
            "fast_p50_under_slow": result.fast_p50_under_slow,
            "hol_ratio": result.hol_ratio,
            "serialized": result.serialized,
        }
    )


# Canonical set of data-plane probe names — the single source of truth shared by
# the local-sim packers below AND any remote probe implementation (e.g. the
# cb-adapters-enterprise cb-probe server). Both sides MUST register exactly these
# names; the guards below turn any drift (a renamed/added/removed probe) into a
# loud failure at import/build time instead of a silent change in scoring output.
PROBE_NAMES: frozenset[str] = frozenset(
    {
        "sustained_load",
        "warm_retention",
        "soak",
        "rate_limit",
        "cancellation",
        "ttft",
        "concurrency_ceiling",
        "scaling",
        "fault_recovery",
        "retry_storm",
        "hol_blocking",
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


def _assert_conforms(names: set[str] | frozenset[str], *, who: str) -> None:
    """Raise if ``names`` drifts from the canonical PROBE_NAMES."""
    names = set(names)
    missing = PROBE_NAMES - names
    extra = names - PROBE_NAMES
    if missing or extra:
        raise RuntimeError(
            f"{who} drifted from PROBE_NAMES (single source of truth): "
            f"missing={sorted(missing)} extra={sorted(extra)}"
        )


# Local-sim packers must cover exactly the canonical set.
_assert_conforms(set(DATA_PLANE_PACKERS), who="DATA_PLANE_PACKERS")


def run_data_plane_probe(
    adapter: AgentRuntimeAdapter,
    name: str,
    params: dict[str, Any] | None = None,
) -> ObservationBundle:
    packer = DATA_PLANE_PACKERS.get(name)
    if packer is None:
        raise ValueError(f"unknown data-plane probe {name!r}; known: {sorted(DATA_PLANE_PACKERS)}")
    return packer(adapter, params or {})
