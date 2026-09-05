"""Official phase-machine orchestration — Load / Power / Throughput / ACID.

Engine-agnostic: all engine specifics (running a query, digesting rows, applying
RF1/RF2, opening a fresh connection) are injected as callables, so ``tpc-ds`` can
reuse this by swapping the closures. Produces the ``official.json`` document the
:class:`OfficialTpchQphhEvaluator` scores.
"""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import Any

from clousight_bench.suites._tpc_official.acid import run_acid
from clousight_bench.suites._tpc_official.streams import run_throughput

ExecuteQuery = Callable[[Any, int], list[Any]]  # (connection, query_nr) -> rows
Digest = Callable[[list[Any]], str]
Refresh = Callable[[Any, int], None]  # (connection, n_rows) -> None


def run_power(
    con: Any,
    *,
    execute_query: ExecuteQuery,
    digest: Digest,
    rf1: Refresh,
    rf2: Refresh,
    n_refresh: int,
    power_order: list[int],
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    """Single-stream Power test: RF1 → queries (stream-0 order) → RF2, all timed."""
    t = clock()
    rf1(con, n_refresh)
    rf1_s = clock() - t

    queries: list[dict[str, Any]] = []
    for nr in power_order:
        t = clock()
        rows = execute_query(con, nr)
        interval_s = clock() - t
        queries.append(
            {
                "query_nr": int(nr),
                "interval_s": interval_s,
                "row_count": len(rows),
                "result_digest": digest(rows),
            }
        )

    t = clock()
    rf2(con, n_refresh)
    rf2_s = clock() - t
    return {"rf1_s": rf1_s, "rf2_s": rf2_s, "queries": queries}


def run_official(
    *,
    con: Any,
    open_conn: Callable[[], Any],
    execute_query: ExecuteQuery,
    digest: Digest,
    rf1: Refresh,
    rf2: Refresh,
    n_refresh: int,
    scale_factor: float,
    power_order: list[int],
    throughput_orders: list[list[int]],
    load_time_s: float,
    engine_meta: dict[str, Any],
    ordering_source: str = "official-appendix-a",
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    """Run the full official pipeline and return the ``official.json`` document.

    ``con`` drives the Power test and ACID probes; each throughput query stream and
    the refresh stream get their own connection from ``open_conn`` (same database)
    so DuckDB MVCC isolates them. ``ordering_source`` is recorded as provenance (the
    query-stream permutations are either the official Appendix A table or a
    clousight-generated ordering).
    """
    doc: dict[str, Any] = {
        "scale_factor": float(scale_factor),
        "streams": len(throughput_orders),
        "ordering_source": ordering_source,
        "load": {"load_time_s": float(load_time_s)},
    }
    doc["power"] = run_power(
        con,
        execute_query=execute_query,
        digest=digest,
        rf1=rf1,
        rf2=rf2,
        n_refresh=n_refresh,
        power_order=power_order,
        clock=clock,
    )

    stream_conns = {sid: open_conn() for sid in range(1, len(throughput_orders) + 1)}
    refresh_conn = open_conn()
    try:

        def run_query(stream_id: int, query_nr: int) -> dict[str, Any]:
            cur = stream_conns[stream_id]
            t = clock()
            rows = execute_query(cur, query_nr)
            interval_s = clock() - t
            return {
                "query_nr": int(query_nr),
                "interval_s": interval_s,
                "row_count": len(rows),
                "result_digest": digest(rows),
            }

        def run_refresh_pair(pair: int) -> dict[str, Any]:
            t = clock()
            rf1(refresh_conn, n_refresh)
            rf1_s = clock() - t
            t = clock()
            rf2(refresh_conn, n_refresh)
            rf2_s = clock() - t
            return {"pair": int(pair), "rf1_s": rf1_s, "rf2_s": rf2_s}

        doc["throughput"] = run_throughput(throughput_orders, run_query, run_refresh_pair, clock=clock)
    finally:
        for c in stream_conns.values():
            c.close()
        refresh_conn.close()

    doc["acid"] = run_acid(con, open_conn)
    doc["engine"] = dict(engine_meta)
    return doc


def run_power_queries(
    con: Any,
    *,
    execute_query: ExecuteQuery,
    digest: Digest,
    power_order: list[int],
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    """TPC-DS-shaped Power test: the ordered query set, no refresh functions."""
    queries: list[dict[str, Any]] = []
    for nr in power_order:
        t = clock()
        rows = execute_query(con, nr)
        interval_s = clock() - t
        queries.append(
            {
                "query_nr": int(nr),
                "interval_s": interval_s,
                "row_count": len(rows),
                "result_digest": digest(rows),
            }
        )
    return {"queries": queries}


def run_official_ds(
    *,
    con: Any,
    open_conn: Callable[[], Any],
    execute_query: ExecuteQuery,
    digest: Digest,
    run_dm: Callable[[Any, int], None],
    n_dm_rows: int,
    scale_factor: float,
    power_order: list[int],
    throughput_orders: list[list[int]],
    load_time_s: float,
    ordering_source: str,
    engine_meta: dict[str, Any],
    acid: Callable[[Any, Callable[[], Any]], dict[str, str]],
    clock: Callable[[], float] = perf_counter,
) -> dict[str, Any]:
    """The TPC-DS official sequence: Power → TT1 → DM1 → TT2 → DM2 (+ ACID gate).

    Data maintenance runs BETWEEN throughput tests (spec sequence), each an
    insert+delete round-trip on the fact table (clousight-generated set).
    """
    doc: dict[str, Any] = {
        "scale_factor": float(scale_factor),
        "streams": len(throughput_orders),
        "ordering_source": ordering_source,
        "load": {"load_time_s": float(load_time_s)},
    }
    doc["power"] = run_power_queries(
        con, execute_query=execute_query, digest=digest, power_order=power_order, clock=clock
    )

    def _throughput_once() -> dict[str, Any]:
        stream_conns = {sid: open_conn() for sid in range(1, len(throughput_orders) + 1)}
        try:

            def run_query(stream_id: int, query_nr: int) -> dict[str, Any]:
                cur = stream_conns[stream_id]
                t = clock()
                rows = execute_query(cur, query_nr)
                interval_s = clock() - t
                return {
                    "query_nr": int(query_nr),
                    "interval_s": interval_s,
                    "row_count": len(rows),
                    "result_digest": digest(rows),
                }

            return run_throughput(throughput_orders, run_query, None, clock=clock)
        finally:
            for c in stream_conns.values():
                c.close()

    def _dm_once() -> dict[str, Any]:
        t = clock()
        run_dm(con, n_dm_rows)
        return {"elapsed_s": clock() - t, "rows": int(n_dm_rows)}

    doc["throughput1"] = _throughput_once()
    doc["dm1"] = _dm_once()
    doc["throughput2"] = _throughput_once()
    doc["dm2"] = _dm_once()
    doc["acid"] = acid(con, open_conn)
    doc["engine"] = dict(engine_meta)
    return doc
