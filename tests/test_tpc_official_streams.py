"""Engine-agnostic multi-stream Throughput orchestration (fake callables, no duckdb)."""

from __future__ import annotations

import threading

import pytest

from clousight_bench.suites._tpc_official.streams import resolve_orders, run_throughput


def test_resolve_orders_power_is_stream0_and_throughput_are_1_plus() -> None:
    table = {"0": [14, 2, 9], "1": [21, 3, 18], "2": [6, 17, 14]}
    power, throughput = resolve_orders(table, num_streams=2)
    assert power == [14, 2, 9]
    assert throughput == [[21, 3, 18], [6, 17, 14]]


def test_resolve_orders_raises_when_table_too_small() -> None:
    table = {"0": [1], "1": [1]}
    with pytest.raises(ValueError, match="query_order.json"):
        resolve_orders(table, num_streams=2)  # needs streams 1 and 2


def test_run_throughput_runs_all_streams_and_refresh_concurrently() -> None:
    throughput_orders = [[21, 3, 18], [6, 17, 14]]
    seen: dict[int, list[int]] = {}
    lock = threading.Lock()

    def run_query(stream_id: int, query_nr: int) -> dict:
        with lock:
            seen.setdefault(stream_id, []).append(query_nr)
        return {"query_nr": query_nr, "interval_s": 0.01, "row_count": 1, "result_digest": f"d{query_nr}"}

    refresh_calls: list[int] = []

    def run_refresh_pair(pair: int) -> dict:
        with lock:
            refresh_calls.append(pair)
        return {"pair": pair, "rf1_s": 0.02, "rf2_s": 0.03}

    out = run_throughput(throughput_orders, run_query, run_refresh_pair)

    assert out["elapsed_s"] > 0
    assert len(out["query_streams"]) == 2
    # each stream ran its queries in its own permutation order
    assert seen[1] == [21, 3, 18]
    assert seen[2] == [6, 17, 14]
    assert [s["stream_id"] for s in out["query_streams"]] == [1, 2]
    assert out["query_streams"][0]["queries"][0]["query_nr"] == 21
    # refresh stream ran S=2 sequential pairs
    assert refresh_calls == [1, 2]
    assert [p["pair"] for p in out["refresh_stream"]] == [1, 2]
