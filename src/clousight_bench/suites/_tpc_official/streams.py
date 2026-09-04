"""Multi-stream Throughput orchestration — engine-agnostic.

The Throughput test runs ``S`` query streams concurrently (each executing all
queries in its own official permutation) alongside a single refresh stream that
runs ``S`` sequential ``(RF1, RF2)`` pairs. This module owns the concurrency and
the elapsed-wall-clock window; the actual query/refresh execution is injected as
callables, so a DuckDB suite and a future config-connect suite share it verbatim.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from typing import Any

QueryResult = dict[str, Any]  # {"query_nr","interval_s","row_count","result_digest"}

# Official TPC-H minimum query-stream count by scale factor (spec clause 5.4.1).
_MIN_STREAMS_BY_SF: tuple[tuple[float, int], ...] = (
    (1, 2),
    (10, 3),
    (30, 4),
    (100, 5),
    (300, 6),
    (1000, 7),
    (3000, 8),
    (10000, 9),
    (30000, 10),
    (100000, 11),
)


def official_min_streams(scale_factor: float) -> int:
    """The official minimum number of query streams S for a scale factor."""
    sf = float(scale_factor)
    chosen = _MIN_STREAMS_BY_SF[0][1]
    for threshold, streams in _MIN_STREAMS_BY_SF:
        if sf >= threshold:
            chosen = streams
    return chosen


def resolve_orders(table: dict[str, list[int]], *, num_streams: int) -> tuple[list[int], list[list[int]]]:
    """Split the official permutation table into (power stream 0, throughput streams 1..S).

    Raises ``ValueError`` naming ``query_order.json`` when the table lacks enough
    streams for the requested ``num_streams`` (extend it from TPC-H Appendix A).
    """
    try:
        power = list(table["0"])
    except KeyError as exc:
        raise ValueError("query_order.json is missing stream 0 (the Power stream)") from exc
    throughput: list[list[int]] = []
    for sid in range(1, num_streams + 1):
        key = str(sid)
        if key not in table:
            raise ValueError(
                f"query_order.json has no stream {sid}; {num_streams} throughput streams "
                "requested — extend the table from TPC-H Appendix A"
            )
        throughput.append(list(table[key]))
    return power, throughput


def run_throughput(
    throughput_orders: list[list[int]],
    run_query: Callable[[int, int], QueryResult],
    run_refresh_pair: Callable[[int], dict[str, Any]],
    *,
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    """Run ``S`` query streams + one refresh stream concurrently.

    ``run_query(stream_id, query_nr)`` returns a per-query result dict;
    ``run_refresh_pair(pair)`` returns ``{"pair","rf1_s","rf2_s"}``. Stream ids are
    1-based (matching the permutation table); the refresh stream runs ``S`` pairs.
    Returns ``{"elapsed_s", "query_streams", "refresh_stream"}``.
    """
    num_streams = len(throughput_orders)

    def _query_stream(stream_id: int, order: list[int]) -> dict[str, Any]:
        return {
            "stream_id": stream_id,
            "queries": [run_query(stream_id, nr) for nr in order],
        }

    def _refresh_stream() -> list[dict[str, Any]]:
        return [run_refresh_pair(pair) for pair in range(1, num_streams + 1)]

    start = clock()
    with ThreadPoolExecutor(max_workers=num_streams + 1) as pool:
        query_futures = [
            pool.submit(_query_stream, sid, order) for sid, order in enumerate(throughput_orders, start=1)
        ]
        refresh_future = pool.submit(_refresh_stream)
        query_streams = [f.result() for f in query_futures]
        refresh_stream = refresh_future.result()
    elapsed_s = clock() - start

    return {
        "elapsed_s": elapsed_s,
        "query_streams": query_streams,
        "refresh_stream": refresh_stream,
    }
