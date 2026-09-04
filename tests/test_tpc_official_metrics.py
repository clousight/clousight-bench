"""Pure official TPC-H metric formulas (Power@Size / Throughput@Size / QphH@Size)."""

from __future__ import annotations

import math

import pytest

from clousight_bench.suites._tpc_official.metrics import (
    geomean,
    power_at_size,
    qphh_at_size,
    throughput_at_size,
)


def test_geomean_basic() -> None:
    assert math.isclose(geomean([1.0, 1.0, 1.0]), 1.0)
    assert math.isclose(geomean([2.0, 8.0]), 4.0)  # sqrt(16)


def test_geomean_drops_nonpositive_and_raises_when_empty() -> None:
    # non-positive wall-times are dropped (perf_counter deltas are always > 0;
    # this guards a degenerate/crafted artifact from log(0)/log(-x)).
    assert math.isclose(geomean([0.0, 2.0, 8.0]), 4.0)
    with pytest.raises(ValueError):
        geomean([])
    with pytest.raises(ValueError):
        geomean([0.0, -1.0])


def test_power_at_size_unit_intervals() -> None:
    # 24 unit intervals -> geomean 1.0 -> Power = 3600 * SF
    qi = [1.0] * 22
    ri = [1.0, 1.0]
    assert math.isclose(power_at_size(qi, ri, scale_factor=1.0), 3600.0)
    assert math.isclose(power_at_size(qi, ri, scale_factor=10.0), 36000.0)


def test_throughput_at_size() -> None:
    # S=2 streams, 22 queries, Ts=158.4s, SF=1 -> (2*22/158.4)*3600*1 = 1000
    val = throughput_at_size(num_streams=2, num_queries=22, elapsed_s=158.4, scale_factor=1.0)
    assert math.isclose(val, 1000.0, rel_tol=1e-9)


def test_qphh_is_geometric_mean_of_power_and_throughput() -> None:
    assert math.isclose(qphh_at_size(3600.0, 1000.0), math.sqrt(3_600_000.0), rel_tol=1e-12)


def test_throughput_rejects_nonpositive_elapsed() -> None:
    with pytest.raises(ValueError):
        throughput_at_size(num_streams=2, num_queries=22, elapsed_s=0.0, scale_factor=1.0)


def test_metrics_scale_linearly_with_scale_factor() -> None:
    # @Size: at a fixed timing profile, all three official numbers scale with SF.
    qi, ri = [1.0] * 22, [1.0, 1.0]
    p1 = power_at_size(qi, ri, scale_factor=1.0)
    p10 = power_at_size(qi, ri, scale_factor=10.0)
    assert math.isclose(p10, 10 * p1)

    t1 = throughput_at_size(num_streams=3, num_queries=22, elapsed_s=200.0, scale_factor=1.0)
    t10 = throughput_at_size(num_streams=3, num_queries=22, elapsed_s=200.0, scale_factor=10.0)
    assert math.isclose(t10, 10 * t1)

    assert math.isclose(qphh_at_size(p10, t10), 10 * qphh_at_size(p1, t1))


def test_throughput_grows_with_stream_count() -> None:
    two = throughput_at_size(num_streams=2, num_queries=22, elapsed_s=200.0, scale_factor=1.0)
    five = throughput_at_size(num_streams=5, num_queries=22, elapsed_s=200.0, scale_factor=1.0)
    assert math.isclose(five / two, 2.5)
