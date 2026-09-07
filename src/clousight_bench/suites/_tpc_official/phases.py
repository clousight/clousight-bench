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

from clousight_bench.suites._progress import INERT_TPC_PROGRESS, TpcProgress
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
    progress: TpcProgress = INERT_TPC_PROGRESS,
) -> dict[str, Any]:
    """Single-stream Power test: RF1 → queries (stream-0 order) → RF2, all timed.

    Power@Size is a geomean over the 24 measured intervals, so the gaps between
    them are not part of any metric: that is where the per-query progress report
    (and the cancel poll) lands.
    """
    progress.phase("Power", total=len(power_order), unit="query")
    progress.log(f"power run: {len(power_order)} queries")
    power_start = clock()

    t = clock()
    rf1(con, n_refresh)
    end = clock()
    rf1_s = end - t
    progress.interval("rf1", t, end, parent="power")

    queries: list[dict[str, Any]] = []
    for nr in power_order:
        t = clock()
        rows = execute_query(con, nr)
        end = clock()
        interval_s = end - t
        queries.append(
            {
                "query_nr": int(nr),
                "interval_s": interval_s,
                "row_count": len(rows),
                "result_digest": digest(rows),
            }
        )
        progress.query(f"q{nr}", t, end, parent="power", interval_s=interval_s)
        progress.check_cancel("power test")

    t = clock()
    rf2(con, n_refresh)
    end = clock()
    rf2_s = end - t
    progress.interval("rf2", t, end, parent="power")
    progress.interval("power", power_start, end, parent="official")
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
    progress: TpcProgress = INERT_TPC_PROGRESS,
) -> dict[str, Any]:
    """Run the full official pipeline and return the ``official.json`` document.

    ``con`` drives the Power test and ACID probes; each throughput query stream and
    the refresh stream get their own connection from ``open_conn`` (same database)
    so DuckDB MVCC isolates them. ``ordering_source`` is recorded as provenance (the
    query-stream permutations are either the official Appendix A table or a
    clousight-generated ordering).

    ``progress`` reports the phases live. The Throughput test is the exception:
    its ``elapsed_s`` IS ``Throughput@Size``, so nothing is written inside that
    window — only a cached cancel poll per stream — and its steps are replayed
    from the measured intervals once it has closed.
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
        progress=progress,
    )
    progress.check_cancel("power test")

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

        total_queries = sum(len(order) for order in throughput_orders)
        progress.phase("Throughput Test", total=total_queries, unit="query", reports_progress=False)
        progress.log(f"throughput test: {len(throughput_orders)} streams, {total_queries} queries total")
        tp_start = clock()
        doc["throughput"] = run_throughput(
            throughput_orders,
            run_query,
            run_refresh_pair,
            clock=clock,
            poll=lambda: progress.check_cancel("throughput test"),
        )
    finally:
        for c in stream_conns.values():
            c.close()
        refresh_conn.close()
    progress.replay_throughput(doc["throughput"], start=tp_start)

    progress.phase("ACID", total=1, unit="probe")
    doc["acid"] = run_acid(con, open_conn)
    progress.advance()
    doc["engine"] = dict(engine_meta)
    progress.finish_root()
    return doc


def run_power_queries(
    con: Any,
    *,
    execute_query: ExecuteQuery,
    digest: Digest,
    power_order: list[int],
    clock: Callable[[], float] = perf_counter,
    progress: TpcProgress = INERT_TPC_PROGRESS,
) -> dict[str, Any]:
    """TPC-DS-shaped Power test: the ordered query set, no refresh functions.

    ``T_Power`` is the SUM of the measured per-query intervals, so the gaps the
    per-query progress reports occupy are outside every metric.
    """
    progress.phase("Power", total=len(power_order), unit="query")
    progress.log(f"power run: {len(power_order)} queries")
    power_start = clock()
    queries: list[dict[str, Any]] = []
    for nr in power_order:
        t = clock()
        rows = execute_query(con, nr)
        end = clock()
        interval_s = end - t
        queries.append(
            {
                "query_nr": int(nr),
                "interval_s": interval_s,
                "row_count": len(rows),
                "result_digest": digest(rows),
            }
        )
        progress.query(f"q{nr}", t, end, parent="power", interval_s=interval_s)
        progress.check_cancel("power test")
    progress.interval("power", power_start, clock(), parent="official")
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
    progress: TpcProgress = INERT_TPC_PROGRESS,
) -> dict[str, Any]:
    """The TPC-DS official sequence: Power → TT1 → DM1 → TT2 → DM2 (+ ACID gate).

    Data maintenance runs BETWEEN throughput tests (spec sequence), each an
    insert+delete round-trip on the fact table (clousight-generated set).

    ``progress`` reports each of those as its own phase. Both throughput tests
    and both maintenance passes are timed by wall clock and feed ``T_TT`` /
    ``T_DM``, so nothing is written inside them: their steps are replayed from
    the measured intervals afterwards.
    """
    doc: dict[str, Any] = {
        "scale_factor": float(scale_factor),
        "streams": len(throughput_orders),
        "ordering_source": ordering_source,
        "load": {"load_time_s": float(load_time_s)},
    }
    doc["power"] = run_power_queries(
        con,
        execute_query=execute_query,
        digest=digest,
        power_order=power_order,
        clock=clock,
        progress=progress,
    )
    progress.check_cancel("power test")
    total_queries = sum(len(order) for order in throughput_orders)

    def _throughput_once(label: str, phase: str) -> dict[str, Any]:
        progress.phase(label, total=total_queries, unit="query", reports_progress=False)
        progress.log(f"{label.lower()}: {len(throughput_orders)} streams, {total_queries} queries total")
        stream_conns = {sid: open_conn() for sid in range(1, len(throughput_orders) + 1)}
        start = clock()
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

            tp = run_throughput(
                throughput_orders,
                run_query,
                None,
                clock=clock,
                poll=lambda: progress.check_cancel(label.lower()),
            )
        finally:
            for c in stream_conns.values():
                c.close()
        progress.replay_throughput(tp, start=start, phase=phase)
        return tp

    def _dm_once(label: str, phase: str) -> dict[str, Any]:
        progress.check_cancel(label.lower())
        progress.phase(label, total=1, unit="pass", reports_progress=False)
        progress.log(f"{label.lower()}: {int(n_dm_rows)} rows")
        t = clock()
        run_dm(con, n_dm_rows)
        end = clock()
        progress.interval(phase, t, end, parent="official")
        progress.advance()
        return {"elapsed_s": end - t, "rows": int(n_dm_rows)}

    doc["throughput1"] = _throughput_once("Throughput Test 1", "throughput1")
    doc["dm1"] = _dm_once("Data Maintenance 1", "dm1")
    doc["throughput2"] = _throughput_once("Throughput Test 2", "throughput2")
    doc["dm2"] = _dm_once("Data Maintenance 2", "dm2")
    progress.phase("ACID", total=1, unit="probe")
    doc["acid"] = acid(con, open_conn)
    progress.advance()
    doc["engine"] = dict(engine_meta)
    progress.finish_root()
    return doc
