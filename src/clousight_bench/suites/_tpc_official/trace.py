"""Reconstruct the official run's OTel-native span tree from its measured timings.

The phase machine measures every interval (Load, Power's 24 windows, each
throughput stream's queries, refresh pairs) into ``official.json``; this module
lays those intervals out as schema-v3 spans (see ``core/sut_span.py``) from one
wall-clock anchor — a faithful reconstruction because the pipeline's concurrency
model is known: phases are sequential, Power is a single ordered stream,
throughput query streams run concurrently from the phase start with their
queries back-to-back, and the refresh stream runs its pairs sequentially in
parallel with them.

Pure functions over plain dicts (no SDK objects): deterministic span ids come
from a sha256 counter so fixtures and tests are stable.
"""

from __future__ import annotations

import hashlib
from typing import Any

_NS = 1_000_000_000


def _ns(seconds: float) -> int:
    return int(seconds * _NS)


class _Ids:
    """Deterministic 16-hex span ids: sha256(f"{trace_id}:{counter}")."""

    def __init__(self, trace_id: str) -> None:
        self._trace_id = trace_id
        self._n = 0

    def next(self) -> str:
        self._n += 1
        return hashlib.sha256(f"{self._trace_id}:{self._n}".encode()).hexdigest()[:16]

    def span(
        self,
        name: str,
        start_ns: int,
        end_ns: int,
        attributes: dict[str, Any],
        parent: str = "",
    ) -> dict[str, Any]:
        return {
            "trace_id": self._trace_id,
            "span_id": self.next(),
            "parent_span_id": parent,
            "name": name,
            "start_unix_nano": start_ns,
            "end_unix_nano": max(end_ns, start_ns),
            "status": "OK",
            "attributes": attributes,
        }


def build_official_spans(
    doc: dict[str, Any],
    *,
    trace_id: str,
    anchor_ns: int,
    suite_id: str,
    engine: str,
) -> list[dict[str, Any]]:
    """Span tree for one official run: ``anchor_ns`` is the wall-clock ns at which
    the Power phase began (run() start); the Load span is laid to END there."""
    ids = _Ids(trace_id)
    spans: list[dict[str, Any]] = []
    base = {"csbench.suite_id": suite_id}

    # --- load (measured in prepare; ends at the anchor) -----------------------
    load_s = float((doc.get("load") or {}).get("load_time_s") or 0.0)
    cursor = anchor_ns
    load_span = ids.span(
        f"{suite_id}.load",
        anchor_ns - _ns(load_s),
        anchor_ns,
        {**base, "csbench.phase": "load"},
    )

    # --- power: RF1 -> Q(stream 0 order) -> RF2, sequential --------------------
    power = doc.get("power") or {}
    power_start = cursor
    power_children: list[dict[str, Any]] = []
    rf1_s = float(power.get("rf1_s") or 0.0)
    power_children.append(
        ids.span(
            f"{suite_id}.rf1",
            cursor,
            cursor + _ns(rf1_s),
            {**base, "csbench.phase": "power", "csbench.refresh": "rf1"},
        )
    )
    cursor += _ns(rf1_s)
    for q in power.get("queries") or []:
        dur = _ns(float(q.get("interval_s") or 0.0))
        power_children.append(
            ids.span(
                f"{suite_id}.q{q.get('query_nr')}",
                cursor,
                cursor + dur,
                {
                    **base,
                    "csbench.phase": "power",
                    "db.system.name": engine,
                    "db.operation.name": f"query {q.get('query_nr')}",
                    "csbench.row_count": int(q.get("row_count") or 0),
                },
            )
        )
        cursor += dur
    rf2_s = float(power.get("rf2_s") or 0.0)
    power_children.append(
        ids.span(
            f"{suite_id}.rf2",
            cursor,
            cursor + _ns(rf2_s),
            {**base, "csbench.phase": "power", "csbench.refresh": "rf2"},
        )
    )
    cursor += _ns(rf2_s)
    power_span = ids.span(f"{suite_id}.power", power_start, cursor, {**base, "csbench.phase": "power"})

    # --- throughput: S concurrent query streams + one refresh stream ----------
    tp = doc.get("throughput") or {}
    tp_start = cursor
    tp_end = tp_start + _ns(float(tp.get("elapsed_s") or 0.0))
    tp_children: list[dict[str, Any]] = []
    for stream in tp.get("query_streams") or []:
        sid = int(stream.get("stream_id") or 0)
        s_cursor = tp_start
        stream_queries: list[dict[str, Any]] = []
        for q in stream.get("queries") or []:
            dur = _ns(float(q.get("interval_s") or 0.0))
            stream_queries.append(
                ids.span(
                    f"{suite_id}.s{sid}.q{q.get('query_nr')}",
                    s_cursor,
                    s_cursor + dur,
                    {
                        **base,
                        "csbench.phase": "throughput",
                        "csbench.stream_id": sid,
                        "db.system.name": engine,
                        "db.operation.name": f"query {q.get('query_nr')}",
                    },
                )
            )
            s_cursor += dur
        stream_span = ids.span(
            f"{suite_id}.stream{sid}",
            tp_start,
            s_cursor,
            {**base, "csbench.phase": "throughput", "csbench.stream_id": sid},
        )
        tp_children.append(stream_span)
        for q_span in stream_queries:
            q_span["parent_span_id"] = stream_span["span_id"]
            tp_children.append(q_span)
    r_cursor = tp_start
    refresh_children: list[dict[str, Any]] = []
    for pair in tp.get("refresh_stream") or []:
        dur = _ns(float(pair.get("rf1_s") or 0.0)) + _ns(float(pair.get("rf2_s") or 0.0))
        refresh_children.append(
            ids.span(
                f"{suite_id}.refresh-pair{pair.get('pair')}",
                r_cursor,
                r_cursor + dur,
                {**base, "csbench.phase": "throughput", "csbench.refresh": f"pair-{pair.get('pair')}"},
            )
        )
        r_cursor += dur
    tp_span = ids.span(f"{suite_id}.throughput", tp_start, tp_end, {**base, "csbench.phase": "throughput"})
    for child in refresh_children:
        child["parent_span_id"] = tp_span["span_id"]

    # --- root ------------------------------------------------------------------
    root = ids.span(
        f"{suite_id}.official",
        load_span["start_unix_nano"],
        max(tp_end, cursor),
        {**base, "csbench.phase": "official", "csbench.streams": int(doc.get("streams") or 0)},
    )
    for top in (load_span, power_span, tp_span):
        top["parent_span_id"] = root["span_id"]
    for child in power_children:
        child["parent_span_id"] = power_span["span_id"]
    for stream_or_query in tp_children:
        if not stream_or_query["parent_span_id"]:
            stream_or_query["parent_span_id"] = tp_span["span_id"]

    spans.append(root)
    spans.append(load_span)
    spans.append(power_span)
    spans.extend(power_children)
    spans.append(tp_span)
    spans.extend(tp_children)
    spans.extend(refresh_children)
    return spans


def build_official_ds_spans(
    doc: dict[str, Any],
    *,
    trace_id: str,
    anchor_ns: int,
    suite_id: str,
    engine: str,
) -> list[dict[str, Any]]:
    """Span tree for a TPC-DS-shaped official run: Power → TT1 → DM1 → TT2 → DM2.

    Same reconstruction rules as :func:`build_official_spans`; ``anchor_ns`` is
    the wall-clock ns at which the Power test began (Load ends there).
    """
    ids = _Ids(trace_id)
    base = {"csbench.suite_id": suite_id}
    spans: list[dict[str, Any]] = []

    load_s = float((doc.get("load") or {}).get("load_time_s") or 0.0)
    load_span = ids.span(
        f"{suite_id}.load", anchor_ns - _ns(load_s), anchor_ns, {**base, "csbench.phase": "load"}
    )

    cursor = anchor_ns
    power_start = cursor
    power_children: list[dict[str, Any]] = []
    for q in (doc.get("power") or {}).get("queries") or []:
        dur = _ns(float(q.get("interval_s") or 0.0))
        power_children.append(
            ids.span(
                f"{suite_id}.q{q.get('query_nr')}",
                cursor,
                cursor + dur,
                {
                    **base,
                    "csbench.phase": "power",
                    "db.system.name": engine,
                    "db.operation.name": f"query {q.get('query_nr')}",
                },
            )
        )
        cursor += dur
    power_span = ids.span(f"{suite_id}.power", power_start, cursor, {**base, "csbench.phase": "power"})

    def _throughput_block(key: str, phase: str) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
        nonlocal cursor
        tp = doc.get(key) or {}
        tp_start = cursor
        tp_end = tp_start + _ns(float(tp.get("elapsed_s") or 0.0))
        children: list[dict[str, Any]] = []
        for stream in tp.get("query_streams") or []:
            sid = int(stream.get("stream_id") or 0)
            s_cursor = tp_start
            stream_queries: list[dict[str, Any]] = []
            for q in stream.get("queries") or []:
                dur = _ns(float(q.get("interval_s") or 0.0))
                stream_queries.append(
                    ids.span(
                        f"{suite_id}.{phase}.s{sid}.q{q.get('query_nr')}",
                        s_cursor,
                        s_cursor + dur,
                        {
                            **base,
                            "csbench.phase": phase,
                            "csbench.stream_id": sid,
                            "db.system.name": engine,
                            "db.operation.name": f"query {q.get('query_nr')}",
                        },
                    )
                )
                s_cursor += dur
            stream_span = ids.span(
                f"{suite_id}.{phase}.stream{sid}",
                tp_start,
                s_cursor,
                {**base, "csbench.phase": phase, "csbench.stream_id": sid},
            )
            children.append(stream_span)
            for q_span in stream_queries:
                q_span["parent_span_id"] = stream_span["span_id"]
                children.append(q_span)
        block = ids.span(f"{suite_id}.{phase}", tp_start, tp_end, {**base, "csbench.phase": phase})
        for child in children:
            if not child["parent_span_id"]:
                child["parent_span_id"] = block["span_id"]
        cursor = tp_end
        return children, block, tp_end

    def _dm_block(key: str, phase: str) -> dict[str, Any]:
        nonlocal cursor
        dm = doc.get(key) or {}
        dur = _ns(float(dm.get("elapsed_s") or 0.0))
        span = ids.span(
            f"{suite_id}.{phase}",
            cursor,
            cursor + dur,
            {**base, "csbench.phase": phase, "csbench.dm_rows": int(dm.get("rows") or 0)},
        )
        cursor += dur
        return span

    tt1_children, tt1_span, _ = _throughput_block("throughput1", "throughput1")
    dm1_span = _dm_block("dm1", "dm1")
    tt2_children, tt2_span, _ = _throughput_block("throughput2", "throughput2")
    dm2_span = _dm_block("dm2", "dm2")

    root = ids.span(
        f"{suite_id}.official",
        load_span["start_unix_nano"],
        cursor,
        {**base, "csbench.phase": "official", "csbench.streams": int(doc.get("streams") or 0)},
    )
    for top in (load_span, power_span, tt1_span, dm1_span, tt2_span, dm2_span):
        top["parent_span_id"] = root["span_id"]
    for child in power_children:
        child["parent_span_id"] = power_span["span_id"]

    spans.append(root)
    spans.extend([load_span, power_span, *power_children])
    spans.extend([tt1_span, *tt1_children, dm1_span])
    spans.extend([tt2_span, *tt2_children, dm2_span])
    return spans


def phase_span(
    *,
    trace_id: str,
    name: str,
    start_unix_nano: int,
    end_unix_nano: int,
    attributes: dict[str, Any],
    parent_span_id: str = "",
) -> dict[str, Any]:
    """One measured (not reconstructed) v3 phase span with a deterministic id.

    Used by the subprocess-wrapping suites (BenchBase, YCSB): the Java tool's
    internals are invisible, so each tool invocation gets exactly one span with
    real wall-clock bounds.
    """
    span_id = hashlib.sha256(f"{trace_id}:{name}:{start_unix_nano}".encode()).hexdigest()[:16]
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": name,
        "start_unix_nano": int(start_unix_nano),
        "end_unix_nano": max(int(end_unix_nano), int(start_unix_nano)),
        "status": "OK",
        "attributes": attributes,
    }
