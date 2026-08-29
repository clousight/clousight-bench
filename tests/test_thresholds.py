"""The shared threshold-check model (CLI --assert + pytest assert_run)."""

from __future__ import annotations

from clousight_bench.core.thresholds import check_thresholds

_M = {
    "mmlu.accuracy": {"value": 0.8},
    "mmlu.avg_latency_ms": {"value": 2500.0},
}


def test_all_met_returns_empty() -> None:
    assert check_thresholds(_M, {"mmlu.accuracy": {"min": 0.7}, "mmlu.avg_latency_ms": {"max": 3000}}) == []


def test_min_bound_failure() -> None:
    fails = check_thresholds(_M, {"mmlu.accuracy": {"min": 0.9}})
    assert len(fails) == 1 and "< min 0.9" in fails[0]


def test_max_bound_failure() -> None:
    fails = check_thresholds(_M, {"mmlu.avg_latency_ms": {"max": 1000}})
    assert len(fails) == 1 and "> max 1000" in fails[0]


def test_scalar_shorthand_is_min() -> None:
    assert check_thresholds(_M, {"mmlu.accuracy": 0.7}) == []
    assert check_thresholds(_M, {"mmlu.accuracy": 0.95})  # not met


def test_missing_measurement_is_a_failure() -> None:
    fails = check_thresholds(_M, {"nope.metric": {"min": 1}})
    assert len(fails) == 1 and "not measured" in fails[0]


def test_non_numeric_value_is_a_failure() -> None:
    fails = check_thresholds({"x": {"value": "oops"}}, {"x": {"min": 1}})
    assert len(fails) == 1 and "non-numeric" in fails[0]


def test_bool_is_not_numeric() -> None:
    # a bool must not sneak through as 0/1
    fails = check_thresholds({"x": {"value": True}}, {"x": {"min": 1}})
    assert len(fails) == 1 and "non-numeric" in fails[0]


def test_accepts_measurement_objects_not_only_dicts() -> None:
    from clousight_bench.core.observation import Measurement

    m = {"a": Measurement(value=0.9, unit="ratio")}
    assert check_thresholds(m, {"a": {"min": 0.5}}) == []
    assert check_thresholds(m, {"a": {"min": 0.95}})
