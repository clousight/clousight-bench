"""Pure statistical aggregation over repeated benchmark measurements.

Phase 1C reduces many ResultRecords of *the same benchmark in the same
environment* to one distribution per measurement. Everything here is a pure
function of its inputs -- no records are read, no clocks are consulted -- so an
aggregate is reproducible from the same set of measurements.

Two measurement shapes are summarised differently:

- **numeric** (``int`` / ``float``, never ``bool``): ``n``, ``mean``, ``stdev``,
  ``min``, ``max``, ``p50``, ``p95`` and ``cv`` (coefficient of variation, the
  headline reproducibility signal -- a benchmark whose ``cv`` is 0.4 is noise).
- **categorical** (labels, booleans, anything non-numeric): the value
  distribution, its ``mode`` and the ``agreement`` fraction, because "did every
  repeat agree?" is the only honest summary of a label.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

# A numeric measurement value. ``bool`` is deliberately excluded: it is an
# ``int`` subclass, but a True/False label is categorical, not a quantity.
NumericKind = "numeric"
CategoricalKind = "categorical"


def is_numeric(value: Any) -> bool:
    """True for a real quantity. ``bool`` is a label, not a number."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _nearest_rank(sorted_values: list[float], percentile: float) -> float:
    """Nearest-rank percentile: robust for the small samples a repeat produces.

    Interpolating percentiles is meaningless at ``n=3``; nearest-rank always
    returns an actually-observed value and is defined for every ``n >= 1``.
    """
    if not sorted_values:
        raise ValueError("percentile of an empty sample is undefined")
    rank = math.ceil(percentile / 100.0 * len(sorted_values))
    index = min(max(rank, 1), len(sorted_values)) - 1
    return sorted_values[index]


def summarize_numeric(values: list[float]) -> dict[str, Any]:
    """Distribution of a numeric measurement across repeats."""
    if not values:
        raise ValueError("cannot summarise an empty numeric sample")
    ordered = sorted(float(v) for v in values)
    n = len(ordered)
    mean = statistics.fmean(ordered)
    stdev = statistics.stdev(ordered) if n >= 2 else 0.0
    return {
        "kind": NumericKind,
        "n": n,
        "mean": mean,
        "stdev": stdev,
        "min": ordered[0],
        "max": ordered[-1],
        "p50": statistics.median(ordered),
        "p95": _nearest_rank(ordered, 95),
        "cv": (stdev / mean) if mean not in (0, 0.0) else None,
    }


def _group_key(value: Any) -> Any:
    """A hashable stand-in for grouping. Some measurements carry a non-hashable
    value (e.g. a list of tool-registration paths); grouping on it directly would
    raise ``unhashable type``. We key on a stable serialisation but keep the
    original value for the mode/distribution output."""
    from collections.abc import Hashable

    if isinstance(value, Hashable):
        return value
    import json

    return json.dumps(value, sort_keys=True, default=str)


def summarize_categorical(values: list[Any]) -> dict[str, Any]:
    """Distribution of a label measurement across repeats."""
    if not values:
        raise ValueError("cannot summarise an empty categorical sample")
    counts: dict[Any, int] = {}
    original: dict[Any, Any] = {}  # group key -> first original value seen
    for value in values:
        key = _group_key(value)
        counts[key] = counts.get(key, 0) + 1
        original.setdefault(key, value)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
    mode_key, mode_count = ordered[0]
    return {
        "kind": CategoricalKind,
        "n": len(values),
        "distinct": len(counts),
        "mode": original[mode_key],
        "agreement": mode_count / len(values),
        "values": [[original[key], count] for key, count in ordered],
    }


def _consistent(items: list[Any]) -> tuple[Any, bool]:
    """Return (the single shared value, True) or (None, False) when they differ."""
    unique = {item for item in items}
    if len(unique) == 1:
        return next(iter(unique)), True
    return None, False


def aggregate_measurements(
    measurement_sets: list[dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Aggregate the ``measurements`` maps of several records into distributions.

    A measurement is summarised numerically only when *every* record that
    reports it gives a numeric value; one label anywhere makes the whole
    measurement categorical, so a distribution never silently mixes 12.5 with
    ``"timeout"``. ``unit`` and ``evidence`` are carried through when every
    record agrees, and blanked with a note when they do not -- a measurement
    reported at evidence C in one run and B in another is not one distribution.
    """
    names: list[str] = []
    seen: set[str] = set()
    for measurements in measurement_sets:
        for name in measurements:
            if name not in seen:
                seen.add(name)
                names.append(name)

    aggregates: dict[str, dict[str, Any]] = {}
    for name in sorted(names):
        values: list[Any] = []
        units: list[str] = []
        evidences: list[str] = []
        for measurements in measurement_sets:
            entry = measurements.get(name)
            if not isinstance(entry, dict) or "value" not in entry:
                continue
            values.append(entry["value"])
            units.append(str(entry.get("unit", "")))
            evidences.append(str(entry.get("evidence", "")))
        if not values:
            continue

        notes: list[str] = []
        if all(is_numeric(v) for v in values):
            summary = summarize_numeric(values)
        else:
            summary = summarize_categorical(values)

        unit, unit_ok = _consistent(units)
        if unit_ok:
            summary["unit"] = unit
        else:
            summary["unit"] = ""
            notes.append("mixed units across repeats")

        evidence, evidence_ok = _consistent(evidences)
        if evidence_ok:
            summary["evidence"] = evidence
        else:
            summary["evidence"] = ""
            notes.append("mixed evidence layers across repeats")

        if notes:
            summary["notes"] = notes
        aggregates[name] = summary
    return aggregates
