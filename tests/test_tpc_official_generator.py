"""Deterministic stream-permutation generator (clousight-generated ordering)."""

from __future__ import annotations

from clousight_bench.suites._tpc_official.streams import generate_orders

_Q = list(range(1, 23))  # the 22 TPC-H query ids


def test_shapes_and_each_is_a_permutation() -> None:
    power, throughput = generate_orders(_Q, num_streams=5)
    assert sorted(power) == _Q
    assert len(throughput) == 5
    for order in throughput:
        assert sorted(order) == _Q  # every stream runs all 22 queries exactly once


def test_deterministic_across_calls() -> None:
    a = generate_orders(_Q, num_streams=4)
    b = generate_orders(_Q, num_streams=4)
    assert a == b  # reproducible — no RNG state, no wall-clock


def test_streams_are_distinct_orderings() -> None:
    power, throughput = generate_orders(_Q, num_streams=6)
    all_orders = [power, *throughput]
    as_tuples = {tuple(o) for o in all_orders}
    assert len(as_tuples) == len(all_orders)  # power + 6 throughput streams all differ


def test_scales_past_the_official_table_streams() -> None:
    # the official Appendix A fixture only ships streams 0-2; the generator must
    # produce as many distinct streams as a large scale factor needs (e.g. S=8).
    power, throughput = generate_orders(_Q, num_streams=8)
    assert len(throughput) == 8


def test_respects_a_query_id_subset() -> None:
    subset = [1, 6, 14]
    power, throughput = generate_orders(subset, num_streams=2)
    assert sorted(power) == subset
    for order in throughput:
        assert sorted(order) == subset
