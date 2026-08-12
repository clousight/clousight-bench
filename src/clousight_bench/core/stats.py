"""Small, dependency-free statistics for latency/throughput distributions.

Cloud latency must be reported as a distribution, never a single mean -- a p95
tail is the whole story a mean hides. This is the one place that turns a list of
per-call latencies into percentiles, so every dimension reports them the same
way. Stdlib-only (linear interpolation), so the open-core stays numpy-free.
"""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT_PERCENTILES = (50, 95, 99)


def percentiles(values: Sequence[float], ps: Sequence[int] = DEFAULT_PERCENTILES) -> dict[int, float]:
    """Return ``{p: value}`` for each requested percentile p in 0..100.

    Linear interpolation between closest ranks (the "inclusive" method), so p50
    of an even-length sample is the midpoint of the two central values. An empty
    input yields 0.0 for every p (a benchmark with no samples has no tail)."""
    if not values:
        return {p: 0.0 for p in ps}
    ordered = sorted(values)
    n = len(ordered)
    out: dict[int, float] = {}
    for p in ps:
        if n == 1:
            out[p] = round(float(ordered[0]), 2)
            continue
        rank = (p / 100) * (n - 1)
        lo = int(rank)
        hi = min(lo + 1, n - 1)
        frac = rank - lo
        out[p] = round(ordered[lo] + (ordered[hi] - ordered[lo]) * frac, 2)
    return out


def latency_metrics(values: Sequence[float], unit: str = "ms") -> dict[str, float]:
    """Percentiles as ready-to-record metric keys: ``latency_p95_ms`` etc."""
    return {f"latency_p{p}_{unit}": v for p, v in percentiles(values).items()}
