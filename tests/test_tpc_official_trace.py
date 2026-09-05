"""The official run's measured timings reconstruct into a valid v3 span tree."""

from __future__ import annotations

from clousight_bench.core.sut_span import validate_span
from clousight_bench.suites._tpc_official.trace import build_official_spans

_DOC = {
    "scale_factor": 1.0,
    "streams": 2,
    "load": {"load_time_s": 2.0},
    "power": {
        "rf1_s": 0.5,
        "rf2_s": 0.5,
        "queries": [
            {"query_nr": 14, "interval_s": 1.0, "row_count": 3},
            {"query_nr": 2, "interval_s": 2.0, "row_count": 1},
        ],
    },
    "throughput": {
        "elapsed_s": 10.0,
        "query_streams": [
            {"stream_id": 1, "queries": [{"query_nr": 21, "interval_s": 4.0}]},
            {"stream_id": 2, "queries": [{"query_nr": 6, "interval_s": 5.0}]},
        ],
        "refresh_stream": [{"pair": 1, "rf1_s": 1.0, "rf2_s": 1.0}],
    },
    "acid": {"durability": "n/a"},
}

_ANCHOR = 1_000_000_000_000  # ns


def _spans():
    return build_official_spans(_DOC, trace_id="c" * 32, anchor_ns=_ANCHOR, suite_id="tpc-h", engine="duckdb")


def test_every_span_is_v3_valid():
    for span in _spans():
        validate_span(span)


def test_tree_shape_and_phase_layout():
    spans = _spans()
    by_name = {s["name"]: s for s in spans}
    root = by_name["tpc-h.official"]
    assert root["parent_span_id"] == ""

    load = by_name["tpc-h.load"]
    assert load["parent_span_id"] == root["span_id"]
    assert load["end_unix_nano"] == _ANCHOR  # load ends where power begins
    assert load["end_unix_nano"] - load["start_unix_nano"] == 2_000_000_000

    power = by_name["tpc-h.power"]
    # power = rf1 + q14 + q2 + rf2 = 4.0s
    assert power["end_unix_nano"] - power["start_unix_nano"] == 4_000_000_000
    q14 = by_name["tpc-h.q14"]
    assert q14["parent_span_id"] == power["span_id"]
    assert q14["attributes"]["db.system.name"] == "duckdb"
    # power order preserved: q14 starts right after rf1
    assert q14["start_unix_nano"] == _ANCHOR + 500_000_000


def test_throughput_streams_run_concurrently_from_phase_start():
    spans = _spans()
    by_name = {s["name"]: s for s in spans}
    tp = by_name["tpc-h.throughput"]
    s1 = by_name["tpc-h.stream1"]
    s2 = by_name["tpc-h.stream2"]
    assert s1["start_unix_nano"] == s2["start_unix_nano"] == tp["start_unix_nano"]
    assert s1["parent_span_id"] == tp["span_id"]
    q21 = by_name["tpc-h.s1.q21"]
    assert q21["parent_span_id"] == s1["span_id"]
    assert q21["attributes"]["csbench.stream_id"] == 1
    # throughput window is the measured elapsed, not the sum of streams
    assert tp["end_unix_nano"] - tp["start_unix_nano"] == 10_000_000_000
    pair = by_name["tpc-h.refresh-pair1"]
    assert pair["parent_span_id"] == tp["span_id"]


def test_span_ids_are_deterministic():
    a = _spans()
    b = _spans()
    assert [s["span_id"] for s in a] == [s["span_id"] for s in b]
