"""Aggregate per-item scores (:class:`ItemResult`) into whole-run Measurements.

Pure, dependency-free (stdlib ``math``/``statistics`` only). This is the R1 seam:
the atom of scoring is now a per-item :class:`ItemScore`; a :class:`Measurement`
is an *aggregation* of those. Only ``ok``/``fail`` scores are counted (a ``fail``
is a real 0/low result); ``skip``/``error`` are excluded from the denominator so
a skipped or crashed metric never silently drags a mean down.

Confidence intervals: proportion metrics (all values in {0, 1}) get a Wilson
score interval; other means get a normal-approximation interval. Both are
attached as ``Measurement.ci`` and are advisory (small-N benchmarks).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from clousight_bench.core.observation import ItemResult, Measurement
from clousight_bench.core.stats import percentiles

_SCORED = ("ok", "fail")  # statuses that count toward an aggregate
_Z = 1.96  # ~95% two-sided normal quantile


def scored_values(items: Sequence[ItemResult], metric: str) -> list[float]:
    """The numeric values of every ``ok``/``fail`` score for ``metric`` (bool→float)."""
    out: list[float] = []
    for item in items:
        for s in item.scores:
            if s.metric == metric and s.status in _SCORED:
                out.append(float(s.value))
    return out


def status_counts(items: Sequence[ItemResult], metric: str) -> dict[str, int]:
    """Count each status ({ok, fail, skip, error}) for ``metric`` across items."""
    counts = {"ok": 0, "fail": 0, "skip": 0, "error": 0}
    for item in items:
        for s in item.scores:
            if s.metric == metric and s.status in counts:
                counts[s.status] += 1
    return counts


def _wilson_ci(k: int, n: int, z: float = _Z) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials."""
    if n == 0:
        return (0.0, 0.0)
    phat = k / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = (z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _normal_mean_ci(vals: Sequence[float], z: float = _Z) -> tuple[float, float] | None:
    n = len(vals)
    if n < 2:
        return None
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    half = z * math.sqrt(var / n)
    return (mean - half, mean + half)


def _is_binary(vals: Sequence[float]) -> bool:
    return all(v in (0.0, 1.0) for v in vals)


def aggregate(
    items: Sequence[ItemResult],
    metric: str,
    how: str = "mean",
    *,
    unit: str = "ratio",
    reproducibility_class: str = "deterministic",
    official: bool = True,
    notes: str = "",
    with_ci: bool = True,
) -> Measurement | None:
    """Aggregate ``metric``'s per-item scores into one Measurement.

    ``how`` ∈ {mean, ratio, sum, geomean, p50, p90, p95, p99}. ``mean`` and
    ``ratio`` are synonyms (both average the per-item values, so partial credit in
    [0,1] and boolean pass/fail both work). Returns ``None`` when no item carries a
    scored (ok/fail) value for ``metric`` — the caller omits the dimension.
    """
    vals = scored_values(items, metric)
    if not vals:
        return None
    n = len(vals)
    ci: tuple[float, float] | None = None

    if how in ("mean", "ratio"):
        value: float = sum(vals) / n
        if with_ci:
            if _is_binary(vals):
                ci = _wilson_ci(int(sum(vals)), n)
            else:
                ci = _normal_mean_ci(vals)
    elif how == "sum":
        value = sum(vals)
    elif how == "geomean":
        if any(v <= 0 for v in vals):
            value = 0.0
        else:
            value = math.exp(sum(math.log(v) for v in vals) / n)
    elif how.startswith("p") and how[1:].isdigit():
        value = percentiles(vals, [int(how[1:])])[int(how[1:])]
    else:
        raise ValueError(f"unknown aggregation {how!r}")

    return Measurement(
        value=value,
        unit=unit,
        reproducibility_class=reproducibility_class,
        official=official,
        aggregation=how,
        sample_count=n,
        notes=notes,
        ci=ci,
    )


def aggregate_by_group(
    items: Sequence[ItemResult], metric: str, how: str = "mean", **kwargs
) -> dict[str, Measurement]:
    """Aggregate ``metric`` separately per ``ItemResult.group`` (category breakdown).

    Returns ``{group: Measurement}`` for each non-empty group that has scored
    values. Items with an empty ``group`` are skipped.
    """
    groups: dict[str, list[ItemResult]] = {}
    for item in items:
        if item.group:
            groups.setdefault(item.group, []).append(item)
    out: dict[str, Measurement] = {}
    for group, group_items in groups.items():
        m = aggregate(group_items, metric, how, **kwargs)
        if m is not None:
            out[group] = m
    return out
