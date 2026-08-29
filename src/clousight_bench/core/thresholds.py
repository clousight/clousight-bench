"""Threshold checks turning a run's measurements into a pass/fail gate.

Measurements are heterogeneous (accuracy is higher-is-better; latency/cost are
lower-is-better), so a threshold declares a direction explicitly:

    {"mmlu.accuracy": {"min": 0.7}}          # must be >= 0.7
    {"tpc-c.p99_latency_us": {"max": 5000}}  # must be <= 5000
    {"mmlu.accuracy": 0.7}                    # scalar shorthand == {"min": 0.7}

Shared by the CLI (`csbench run --assert`) and the pytest plugin (`assert_run`)
so a benchmark run can gate CI the same way in both.
"""

from __future__ import annotations

from typing import Any


def _value_of(measurement: Any) -> Any:
    """Read a measurement's numeric value from a dict record or a Measurement."""
    if isinstance(measurement, dict):
        return measurement.get("value")
    return getattr(measurement, "value", None)


def check_thresholds(measurements: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    """Return human-readable failure strings; an empty list means all thresholds met.

    A threshold key that was not measured is a failure (you asked to gate on a
    number the run did not produce). ``min``/``max`` bounds are inclusive.
    """
    failures: list[str] = []
    for key, bound in thresholds.items():
        if key not in measurements:
            failures.append(f"{key}: not measured (no such measurement in the run)")
            continue
        val = _value_of(measurements[key])
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            failures.append(f"{key}: non-numeric value {val!r}")
            continue
        if isinstance(bound, dict):
            lo, hi = bound.get("min"), bound.get("max")
        else:
            lo, hi = bound, None  # scalar shorthand == a minimum
        if lo is not None and val < lo:
            failures.append(f"{key}={val} < min {lo}")
        if hi is not None and val > hi:
            failures.append(f"{key}={val} > max {hi}")
    return failures
