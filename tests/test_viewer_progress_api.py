"""Tests for the viewer's board, suite and progress-plane HTTP API.

Everything is exercised against a real server (create_server on an ephemeral
port, serve_forever on a daemon thread) and a real progress plane written by
``core.progress.ProgressWriter`` — no fakes in front of either.

The SSE tests are driven deterministically rather than by waiting: the client
reads frames until the one it expects, and the *test* decides when the run
finishes by calling ``writer.finish()``. Nothing here sleeps for a fixed
duration, so none of it belongs in the slow bucket.
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import time
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from clousight_bench.core import progress
from clousight_bench.viewer.server import _STREAM_SLOTS, create_server

LIVE_RUN = "run-live-1"


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _write_record(
    results_dir: Path,
    *,
    domain: str,
    adapter: str,
    task_id: str,
    run_id: str,
    started_at: str,
    suite_id: str = "",
    status: str = "completed",
    measurements: dict[str, float] | None = None,
    suite_version: str = "",
    evaluator_id: str = "",
) -> None:
    """One record file in the layout ResultStore writes."""
    record = {
        "run": {"run_id": run_id, "started_at": started_at},
        "identity": {"domain": domain, "task_id": task_id, "adapter": adapter},
        "status": status,
        "provenance": {
            "suite_id": suite_id,
            "suite_version": suite_version,
            "evaluator_id": evaluator_id,
        },
        "measurements": {key: {"value": value, "unit": "ms"} for key, value in (measurements or {}).items()},
        "artifacts": [],
    }
    out = results_dir / domain / adapter
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{task_id}-{run_id}.json").write_text(json.dumps(record), encoding="utf-8")


@pytest.fixture()
def results_dir(tmp_path: Path) -> Path:
    path = tmp_path / "results"
    path.mkdir()
    return path


@pytest.fixture()
def server(results_dir: Path) -> Iterator[ThreadingHTTPServer]:
    srv = create_server(results_dir, host="127.0.0.1", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _request(
    srv: ThreadingHTTPServer,
    path: str,
    method: str = "GET",
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        payload = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, payload
    finally:
        conn.close()


def _json(srv: ThreadingHTTPServer, path: str) -> Any:
    status, _, body = _request(srv, path)
    assert status == 200, (path, status, body)
    return json.loads(body)


def _live_writer(results_dir: Path, run_id: str = LIVE_RUN) -> progress.ProgressWriter:
    """A begun ProgressWriter for this process (so the liveness probe sees it)."""
    writer = progress.ProgressWriter(
        results_dir,
        run_id,
        trace_id="a" * 32,
        domain="data-warehouse",
        task_id="suite:tpc-h",
        suite_id="tpc-h",
        adapter="duckdb-local",
        started_at="2026-01-01T00:00:00Z",
    )
    writer.begin()
    return writer


def _sse_open(srv: ThreadingHTTPServer, path: str) -> tuple[http.client.HTTPConnection, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=15)
    conn.request("GET", path)
    return conn, conn.getresponse()


def _read_frames(resp: Any, *, until: str) -> list[tuple[str, Any]]:
    """Read SSE frames until (and including) one named ``until``, or EOF.

    Comment lines (``: heartbeat``) and the blank frame separators carry no
    payload, so they are consumed and dropped.
    """
    frames: list[tuple[str, Any]] = []
    event = ""
    while True:
        raw = resp.readline()
        if not raw:
            return frames
        line = raw.decode("utf-8").rstrip("\n")
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            frames.append((event, json.loads(line[len("data: ") :])))
            if event == until:
                return frames


def _await_free_slots(limit: int = 8, timeout: float = 10.0) -> None:
    """Block until every SSE slot is back, so one test cannot starve the next."""
    deadline = time.monotonic() + timeout
    while _STREAM_SLOTS._held and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _STREAM_SLOTS._held == 0, f"{_STREAM_SLOTS._held} of {limit} stream slots still held"


# ---------------------------------------------------------------------------
# /api/board
# ---------------------------------------------------------------------------


def _seed_board(results_dir: Path) -> None:
    """Two domains: one suite with two platforms, one with a task_id fallback."""
    for index, started in enumerate(("2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z")):
        _write_record(
            results_dir,
            domain="data-warehouse",
            adapter="duckdb-local",
            task_id="suite:tpc-h",
            run_id=f"run-h{index}",
            started_at=started,
            suite_id="tpc-h",
            measurements={"tpc-h.geomean_latency_ms": 11.4 + index},
            suite_version="3.0.1",
            evaluator_id="tpch-evaluator",
        )
    _write_record(
        results_dir,
        domain="data-warehouse",
        adapter="clickhouse",
        task_id="suite:tpc-h",
        run_id="run-h-ch",
        started_at="2026-01-02T00:00:00Z",
        suite_id="tpc-h",
        measurements={"tpc-h.queries_passed": 22.0},
    )
    _write_record(
        results_dir,
        domain="data-warehouse",
        adapter="duckdb-local",
        task_id="suite:tpc-ds",
        run_id="run-ds",
        started_at="2026-01-04T00:00:00Z",
        suite_id="tpc-ds",
        measurements={"tpc-ds.qphds": 900.0},
    )
    # No suite_id in provenance: the board must fall back to the task_id with
    # the "suite:" prefix stripped.
    _write_record(
        results_dir,
        domain="agent-runtime",
        adapter="local-sim",
        task_id="suite:swe-bench",
        run_id="run-swe",
        started_at="2026-01-05T00:00:00Z",
        measurements={"swe-bench.resolved": 0.5},
    )


def test_board_groups_by_domain_and_suite(server: ThreadingHTTPServer, results_dir: Path) -> None:
    _seed_board(results_dir)
    board = _json(server, "/api/board")

    assert [d["domain"] for d in board["domains"]] == ["agent-runtime", "data-warehouse"]
    warehouse = board["domains"][1]
    assert warehouse["runs"] == 4
    assert [s["suite_id"] for s in warehouse["suites"]] == ["tpc-ds", "tpc-h"]

    tpch = warehouse["suites"][1]
    assert tpch["task_id"] == "suite:tpc-h"
    assert tpch["runs"] == 3
    assert tpch["platforms"] == 2


def test_board_latest_is_the_newest_run_on_any_platform(
    server: ThreadingHTTPServer, results_dir: Path
) -> None:
    _seed_board(results_dir)
    board = _json(server, "/api/board")
    tpch = board["domains"][1]["suites"][1]
    assert tpch["latest"] == {
        "run_id": "run-h1",
        "adapter": "duckdb-local",
        "started_at": "2026-01-03T00:00:00Z",
        "status": "completed",
        "measurements": {"tpc-h.geomean_latency_ms": 12.4},
    }


def test_board_falls_back_to_task_id_without_provenance(
    server: ThreadingHTTPServer, results_dir: Path
) -> None:
    _seed_board(results_dir)
    board = _json(server, "/api/board")
    agent = board["domains"][0]
    assert agent["suites"][0]["suite_id"] == "swe-bench"  # "suite:" prefix stripped
    assert agent["suites"][0]["task_id"] == "suite:swe-bench"


def test_board_is_empty_for_an_empty_results_dir(server: ThreadingHTTPServer) -> None:
    assert _json(server, "/api/board") == {"domains": []}


# ---------------------------------------------------------------------------
# /api/suite/<domain>/<suite_id>
# ---------------------------------------------------------------------------


def test_suite_returns_one_row_per_platform(server: ThreadingHTTPServer, results_dir: Path) -> None:
    _seed_board(results_dir)
    suite = _json(server, "/api/suite/data-warehouse/tpc-h")

    assert suite["domain"] == "data-warehouse"
    assert suite["suite_id"] == "tpc-h"
    assert [p["adapter"] for p in suite["platforms"]] == ["clickhouse", "duckdb-local"]
    duckdb = suite["platforms"][1]
    assert duckdb["runs"] == 2
    assert duckdb["latest"]["run_id"] == "run-h1"
    assert duckdb["latest"]["suite_version"] == "3.0.1"
    assert duckdb["latest"]["evaluator_id"] == "tpch-evaluator"


def test_suite_metric_keys_are_the_sorted_union(server: ThreadingHTTPServer, results_dir: Path) -> None:
    _seed_board(results_dir)
    suite = _json(server, "/api/suite/data-warehouse/tpc-h")
    # geomean comes from the duckdb runs, queries_passed only from clickhouse
    assert suite["metric_keys"] == ["tpc-h.geomean_latency_ms", "tpc-h.queries_passed"]


def test_suite_history_is_oldest_first_and_capped(server: ThreadingHTTPServer, results_dir: Path) -> None:
    for index in range(35):
        _write_record(
            results_dir,
            domain="data-warehouse",
            adapter="duckdb-local",
            task_id="suite:tpc-h",
            run_id=f"run-{index:03d}",
            started_at=f"2026-02-{index + 1:02d}T00:00:00Z",
            suite_id="tpc-h",
            measurements={"tpc-h.geomean_latency_ms": float(index)},
        )
    suite = _json(server, "/api/suite/data-warehouse/tpc-h")
    history = suite["platforms"][0]["history"]

    assert len(history) == 30  # capped at the 30 most recent
    assert [h["run_id"] for h in history] == [f"run-{i:03d}" for i in range(5, 35)]
    assert history[0]["started_at"] < history[-1]["started_at"]
    assert suite["platforms"][0]["latest"]["run_id"] == history[-1]["run_id"]


def test_suite_unknown_pair_is_404(server: ThreadingHTTPServer, results_dir: Path) -> None:
    _seed_board(results_dir)
    for path in (
        "/api/suite/data-warehouse/nope",
        "/api/suite/nope/tpc-h",
        "/api/suite/..%2F..%2Fetc/tpc-h",  # traversal-shaped segment: rejected by the token check
    ):
        status, headers, body = _request(server, path)
        assert status == 404, path
        assert headers["content-type"] == "application/json; charset=utf-8"
        assert "error" in json.loads(body)


# ---------------------------------------------------------------------------
# /api/progress and /api/meta
# ---------------------------------------------------------------------------


def test_api_progress_lists_a_live_run(server: ThreadingHTTPServer, results_dir: Path) -> None:
    writer = _live_writer(results_dir)
    writer.stage_start("EXECUTE")

    payload = _json(server, "/api/progress")
    assert len(payload["runs"]) == 1
    state = payload["runs"][0]
    assert state["run_id"] == LIVE_RUN
    assert state["status"] == "running"
    assert state["stage"] == "EXECUTE"
    assert state["suite_id"] == "tpc-h"
    assert state["pid"] == os.getpid()


def test_api_progress_run_returns_state_and_events(server: ThreadingHTTPServer, results_dir: Path) -> None:
    writer = _live_writer(results_dir)
    writer.stage_start("EXECUTE")
    writer.phase("power run", total=22, unit="query")
    writer.advance(3)

    payload = _json(server, f"/api/progress/{LIVE_RUN}")
    assert payload["state"]["step"]["completed"] == 3
    kinds = [event["kind"] for event in payload["events"]]
    assert kinds == ["stage", "progress", "progress"]
    assert [event["seq"] for event in payload["events"]] == [1, 2, 3]


def test_api_progress_since_filters_events(server: ThreadingHTTPServer, results_dir: Path) -> None:
    writer = _live_writer(results_dir)
    writer.stage_start("EXECUTE")
    writer.phase("power run", total=22)
    writer.advance(1)

    payload = _json(server, f"/api/progress/{LIVE_RUN}?since=2")
    assert [event["seq"] for event in payload["events"]] == [3]
    # junk and negative values read as "from the beginning", never as an error
    for query in ("?since=abc", "?since=-5", ""):
        assert len(_json(server, f"/api/progress/{LIVE_RUN}{query}")["events"]) == 3


def test_api_progress_unknown_run_is_404(server: ThreadingHTTPServer) -> None:
    for path in (f"/api/progress/{LIVE_RUN}", "/api/progress/..%2F..%2Fetc%2Fpasswd"):
        status, headers, body = _request(server, path)
        assert status == 404, path
        assert headers["content-type"] == "application/json; charset=utf-8"
        assert "error" in json.loads(body)


def test_api_meta_counts_active_progress_runs(server: ThreadingHTTPServer, results_dir: Path) -> None:
    assert _json(server, "/api/meta")["progress_active"] == 0
    _live_writer(results_dir)
    meta = _json(server, "/api/meta")
    assert meta["progress_active"] == 1
    assert set(meta) == {"results_dir", "version", "counts", "progress_active"}


def test_create_server_reaps_stale_progress_dirs(results_dir: Path) -> None:
    """A snapshot from a dead pid must not greet the user as a running row."""
    stale = results_dir / progress.PROGRESS_DIRNAME / "run-dead"
    stale.mkdir(parents=True)
    (stale / progress.STATE_FILE).write_text(
        json.dumps(
            {
                "schema": progress.SCHEMA,
                "run_id": "run-dead",
                "pid": 0,  # never a live process
                "status": "running",
                "started_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    old = time.time() - progress.TERMINAL_GRACE_S - 60
    os.utime(stale / progress.STATE_FILE, (old, old))

    srv = create_server(results_dir, host="127.0.0.1", port=0)
    srv.server_close()
    assert not stale.exists()


# ---------------------------------------------------------------------------
# SSE: /api/progress/<run_id>/stream
# ---------------------------------------------------------------------------


def test_sse_sends_state_then_events_then_done(server: ThreadingHTTPServer, results_dir: Path) -> None:
    writer = _live_writer(results_dir)
    writer.stage_start("EXECUTE")

    conn, resp = _sse_open(server, f"/api/progress/{LIVE_RUN}/stream")
    try:
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "text/event-stream; charset=utf-8"
        assert resp.getheader("Cache-Control") == "no-cache"
        assert resp.getheader("X-Accel-Buffering") == "no"

        opening = _read_frames(resp, until="events")
        assert [name for name, _ in opening] == ["state", "events"]
        assert opening[0][1]["status"] == "running"
        assert [event["kind"] for event in opening[1][1]["events"]] == ["stage"]

        # The test decides when the run ends; the stream must notice on its own.
        writer.finish("completed", "results/data-warehouse/duckdb-local/x.json")
        rest = _read_frames(resp, until="done")
        assert rest[-1][0] == "done"
        assert rest[-1][1] == {
            "status": "completed",
            "record_path": "results/data-warehouse/duckdb-local/x.json",
        }
        assert any(name == "state" and data["status"] == "completed" for name, data in rest)
        assert any(
            name == "events" and any(e["kind"] == "done" for e in data["events"]) for name, data in rest
        )
    finally:
        conn.close()
    _await_free_slots()


def test_sse_honours_since_and_ends_on_an_already_finished_run(
    server: ThreadingHTTPServer, results_dir: Path
) -> None:
    writer = _live_writer(results_dir)
    writer.stage_start("EXECUTE")
    writer.log("hello")
    writer.finish("failed")

    conn, resp = _sse_open(server, f"/api/progress/{LIVE_RUN}/stream?since=2")
    try:
        frames = _read_frames(resp, until="done")
        names = [name for name, _ in frames]
        assert names[0] == "state"
        assert names[-1] == "done"
        events = [event for name, data in frames if name == "events" for event in data["events"]]
        assert [event["seq"] for event in events] == [3]  # seq 1 (stage) and 2 (log) are backlog
        assert frames[-1][1]["status"] == "failed"
    finally:
        conn.close()
    _await_free_slots()


def test_sse_ends_when_the_progress_dir_is_reaped(server: ThreadingHTTPServer, results_dir: Path) -> None:
    writer = _live_writer(results_dir)
    writer.stage_start("EXECUTE")

    conn, resp = _sse_open(server, f"/api/progress/{LIVE_RUN}/stream")
    try:
        _read_frames(resp, until="events")
        writer.discard()  # the directory vanishes underneath the subscriber
        frames = _read_frames(resp, until="done")
        assert frames[-1][0] == "done"
        assert frames[-1][1]["status"] == "running"  # the last status we actually held
    finally:
        conn.close()
    _await_free_slots()


def test_sse_unknown_run_is_404_without_taking_a_slot(server: ThreadingHTTPServer) -> None:
    status, headers, body = _request(server, "/api/progress/run-nope/stream")
    assert status == 404
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert "error" in json.loads(body)
    assert _STREAM_SLOTS._held == 0


def test_sse_concurrent_stream_cap_is_503(server: ThreadingHTTPServer, results_dir: Path) -> None:
    writer = _live_writer(results_dir)
    writer.stage_start("EXECUTE")

    open_streams: list[tuple[http.client.HTTPConnection, Any]] = []
    try:
        for _ in range(8):
            conn, resp = _sse_open(server, f"/api/progress/{LIVE_RUN}/stream")
            assert resp.status == 200
            _read_frames(resp, until="events")  # the handler is now inside its poll loop
            open_streams.append((conn, resp))

        status, headers, body = _request(server, f"/api/progress/{LIVE_RUN}/stream")
        assert status == 503
        assert headers["content-type"] == "application/json; charset=utf-8"
        assert json.loads(body) == {"error": "too many progress streams"}
    finally:
        # Finishing the run is how the eight in-flight streams are released.
        writer.finish("completed")
        for conn, resp in open_streams:
            _read_frames(resp, until="done")
            conn.close()
    _await_free_slots()

    # The slots come back: a ninth stream now succeeds.
    conn, resp = _sse_open(server, f"/api/progress/{LIVE_RUN}/stream")
    try:
        assert resp.status == 200
        _read_frames(resp, until="done")
    finally:
        conn.close()
    _await_free_slots()


# ---------------------------------------------------------------------------
# POST /api/progress/<run_id>/cancel
# ---------------------------------------------------------------------------

CANCEL_HEADERS = {"X-Csbench-Progress": "1"}


def _cancel_path(run_id: str = LIVE_RUN) -> str:
    return f"/api/progress/{run_id}/cancel"


def test_cancel_creates_the_marker_for_a_live_run(server: ThreadingHTTPServer, results_dir: Path) -> None:
    writer = _live_writer(results_dir)
    marker = results_dir / progress.PROGRESS_DIRNAME / LIVE_RUN / progress.CANCEL_FILE
    assert not marker.exists()

    status, headers, body = _request(server, _cancel_path(), "POST", headers=CANCEL_HEADERS)
    assert status == 200
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert json.loads(body) == {"cancelled": True}
    assert marker.is_file()
    assert writer.should_cancel() is True


def test_cancel_without_the_custom_header_is_403(server: ThreadingHTTPServer, results_dir: Path) -> None:
    _live_writer(results_dir)
    # A cross-origin <form> can POST, but it cannot set a custom header.
    for headers in ({}, {"X-Csbench-Progress": "0"}, {"Content-Type": "text/plain"}):
        status, _, body = _request(server, _cancel_path(), "POST", headers=headers)
        assert status == 403, headers
        assert "error" in json.loads(body)
    marker = results_dir / progress.PROGRESS_DIRNAME / LIVE_RUN / progress.CANCEL_FILE
    assert not marker.exists()


def test_cancel_with_a_body_is_400(server: ThreadingHTTPServer, results_dir: Path) -> None:
    _live_writer(results_dir)
    status, _, body = _request(
        server, _cancel_path(), "POST", headers=CANCEL_HEADERS, body=b'{"run":"other"}'
    )
    assert status == 400
    assert "error" in json.loads(body)
    marker = results_dir / progress.PROGRESS_DIRNAME / LIVE_RUN / progress.CANCEL_FILE
    assert not marker.exists()


def test_cancel_unknown_run_is_404(server: ThreadingHTTPServer) -> None:
    status, _, body = _request(server, _cancel_path("run-nope"), "POST", headers=CANCEL_HEADERS)
    assert status == 404
    assert json.loads(body)["error"] == "no cancellable run: run-nope"


def test_cancel_already_finished_run_is_404(server: ThreadingHTTPServer, results_dir: Path) -> None:
    writer = _live_writer(results_dir)
    writer.finish("completed", "results/x.json")
    status, _, body = _request(server, _cancel_path(), "POST", headers=CANCEL_HEADERS)
    assert status == 404
    assert "error" in json.loads(body)
    marker = results_dir / progress.PROGRESS_DIRNAME / LIVE_RUN / progress.CANCEL_FILE
    assert not marker.exists()


def test_cancel_rejects_a_traversal_run_id_before_the_filesystem(
    server: ThreadingHTTPServer, results_dir: Path, tmp_path: Path
) -> None:
    """``../../etc/passwd`` must be refused by the token check, not by luck."""
    status, _, body = _request(
        server, "/api/progress/..%2F..%2Fetc%2Fpasswd/cancel", "POST", headers=CANCEL_HEADERS
    )
    assert status == 404
    # The malformed-token branch, not "no cancellable run": nothing was looked up.
    assert json.loads(body)["error"] == "unknown run_id"
    assert not (results_dir / progress.PROGRESS_DIRNAME).exists()
    assert not (tmp_path / "etc").exists()
    assert list(tmp_path.iterdir()) == [results_dir]


def test_unknown_post_path_is_json_404_without_spa_fallback(server: ThreadingHTTPServer) -> None:
    for path in ("/", "/index.html", "/api/records", "/api/progress//cancel", "/api/nope"):
        status, headers, body = _request(server, path, "POST", headers=CANCEL_HEADERS)
        assert status == 404, path
        assert headers["content-type"] == "application/json; charset=utf-8"
        assert "error" in json.loads(body)


def test_post_shares_the_host_guard_with_get(server: ThreadingHTTPServer, results_dir: Path) -> None:
    _live_writer(results_dir)
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    try:
        conn.putrequest("POST", _cancel_path(), skip_host=True)
        conn.putheader("Host", "evil.example")
        conn.putheader("X-Csbench-Progress", "1")
        conn.putheader("Content-Length", "0")
        conn.endheaders()
        resp = conn.getresponse()
        body = resp.read()
    finally:
        conn.close()
    assert resp.status == 403
    assert json.loads(body) == {"error": "host not allowed"}
    marker = results_dir / progress.PROGRESS_DIRNAME / LIVE_RUN / progress.CANCEL_FILE
    assert not marker.exists()
