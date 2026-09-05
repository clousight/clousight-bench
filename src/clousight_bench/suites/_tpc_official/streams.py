"""Multi-stream Throughput orchestration — engine-agnostic.

The Throughput test runs ``S`` query streams concurrently (each executing all
queries in its own official permutation) alongside a single refresh stream that
runs ``S`` sequential ``(RF1, RF2)`` pairs. This module owns the concurrency and
the elapsed-wall-clock window; the actual query/refresh execution is injected as
callables, so a DuckDB suite and a future config-connect suite share it verbatim.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from typing import Any

QueryResult = dict[str, Any]  # {"query_nr","interval_s","row_count","result_digest"}

# Bumped if the generated-ordering algorithm changes (folded into run provenance).
GENERATOR_VERSION = "gen-v1"

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


def _keystream(seed: bytes) -> Iterator[int]:
    """Endless deterministic byte stream from sha256(seed || counter)."""
    counter = 0
    while True:
        yield from hashlib.sha256(seed + counter.to_bytes(8, "big")).digest()
        counter += 1


def _rand_below(ks: Iterator[int], n: int) -> int:
    """Uniform integer in [0, n) drawn from *ks*, rejection-sampled to avoid bias."""
    limit = 256 - (256 % n)  # largest multiple of n <= 256
    while True:
        b = next(ks)
        if b < limit:
            return b % n


def _shuffle(items: Sequence[int], seed: bytes) -> list[int]:
    """Deterministic Fisher-Yates shuffle of *items* seeded by *seed*."""
    arr = list(items)
    ks = _keystream(seed)
    for i in range(len(arr) - 1, 0, -1):
        j = _rand_below(ks, i + 1)
        arr[i], arr[j] = arr[j], arr[i]
    return arr


def generate_orders(query_ids: Sequence[int], *, num_streams: int) -> tuple[list[int], list[list[int]]]:
    """Deterministic (power stream 0, throughput streams 1..S) permutations.

    A **clousight-generated** ordering (NOT the official Appendix A sequences): each
    stream is a distinct, reproducible Fisher-Yates permutation of *query_ids*,
    seeded by the query set + stream index. Lets the Throughput test scale to any S
    the official minimum requires without shipping guessed permutation tables. The
    throughput metric depends only on running all queries per stream, so a valid
    distinct permutation is metric-correct; only strict comparability to a published
    run needs the exact Appendix A order.
    """
    base = ",".join(str(int(q)) for q in query_ids)

    def order_for(stream_id: int) -> list[int]:
        seed = hashlib.sha256(f"{GENERATOR_VERSION}|{base}|{stream_id}".encode()).digest()
        return _shuffle(query_ids, seed)

    power = order_for(0)
    throughput = [order_for(sid) for sid in range(1, int(num_streams) + 1)]
    return power, throughput


def run_throughput(
    throughput_orders: list[list[int]],
    run_query: Callable[[int, int], QueryResult],
    run_refresh_pair: Callable[[int], dict[str, Any]] | None,
    *,
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    """Run ``S`` query streams (+ optionally one refresh stream) concurrently.

    ``run_query(stream_id, query_nr)`` returns a per-query result dict;
    ``run_refresh_pair(pair)`` returns ``{"pair","rf1_s","rf2_s"}`` — TPC-H runs it
    as a concurrent refresh stream of ``S`` pairs; TPC-DS passes ``None`` (its data
    maintenance runs AFTER each throughput test, not alongside). Stream ids are
    1-based (matching the permutation table).
    Returns ``{"elapsed_s", "query_streams", "refresh_stream"}``.
    """
    num_streams = len(throughput_orders)

    def _query_stream(stream_id: int, order: list[int]) -> dict[str, Any]:
        return {
            "stream_id": stream_id,
            "queries": [run_query(stream_id, nr) for nr in order],
        }

    def _refresh_stream() -> list[dict[str, Any]]:
        assert run_refresh_pair is not None  # guarded by the submit-site check
        return [run_refresh_pair(pair) for pair in range(1, num_streams + 1)]

    start = clock()
    with ThreadPoolExecutor(max_workers=num_streams + 1) as pool:
        query_futures = [
            pool.submit(_query_stream, sid, order) for sid, order in enumerate(throughput_orders, start=1)
        ]
        refresh_future = pool.submit(_refresh_stream) if run_refresh_pair is not None else None
        query_streams = [f.result() for f in query_futures]
        refresh_stream = refresh_future.result() if refresh_future is not None else []
    elapsed_s = clock() - start

    return {
        "elapsed_s": elapsed_s,
        "query_streams": query_streams,
        "refresh_stream": refresh_stream,
    }
