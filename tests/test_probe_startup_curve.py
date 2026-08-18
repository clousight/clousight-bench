import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime.probe.dataplane import run_startup_curve
from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec


def _make_server(cold_delay_s: float, fail_calls: set[int] = frozenset()):
    """Data-plane endpoint whose FIRST invoke is slow (cold) then fast (warm).

    Calls whose 1-based index is in ``fail_calls`` return HTTP 500 so the probe
    records them as errors.
    """
    counter = {"n": 0}

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n)
            counter["n"] += 1
            if counter["n"] == 1:
                time.sleep(cold_delay_s)  # simulate cold start on the first invoke
            if counter["n"] in fail_calls:
                self.send_response(500)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
                return
            body = json.dumps({"choices": [{"message": {"content": '{"ok": true}'}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}", counter


def test_startup_curve_derives_cold_warm_metrics():
    srv, base, _ = _make_server(cold_delay_s=0.3)
    try:
        spec = JobSpec(
            probe="startup_curve",
            params={"n_calls": 6, "warm_threshold_ms": 100.0},
            target_endpoint=base,
            mock_base_url="http://mock",
            mock_token="t",
        )
        bundle = run_startup_curve(spec, lambda prog, metrics: None)
    finally:
        srv.shutdown()
    o = bundle.observations
    assert o["capability"] == "supported"
    assert len(o["curve_ms"]) == 6
    # Cold-start dimension: first call paid the 0.3s cold delay.
    assert o["cold_start_ms"] >= 300.0
    assert o["second_call_ms"] < 100.0
    assert o["third_call_ms"] < 100.0
    # Warm steady-state well below the cold start.
    assert o["warm_steady_ms"] is not None and o["warm_steady_ms"] < 100.0
    assert o["speedup_ratio"] is not None and o["speedup_ratio"] > 1.0
    # The 2nd call is the first one that lands in the warm zone.
    assert o["warmed_after_n_calls"] == 2
    assert o["reuse_reliable"] is True
    assert o["errors"] == 0
    assert o["n_calls"] == 6
    assert bundle.series["curve_ms"][0] == [1, o["cold_start_ms"]]


def test_startup_curve_flags_unreliable_on_errors():
    srv, base, _ = _make_server(cold_delay_s=0.0, fail_calls={3, 5})
    try:
        spec = JobSpec(
            probe="startup_curve",
            params={"n_calls": 6, "warm_threshold_ms": 100.0},
            target_endpoint=base,
            mock_base_url="http://mock",
            mock_token="t",
        )
        bundle = run_startup_curve(spec, lambda prog, metrics: None)
    finally:
        srv.shutdown()
    o = bundle.observations
    assert o["errors"] == 2
    # Any error in the sweep breaks reuse reliability even if latencies are fast.
    assert o["reuse_reliable"] is False


def test_startup_curve_progress_reports_each_call():
    srv, base, _ = _make_server(cold_delay_s=0.0)
    seen: list[tuple[int, int]] = []
    try:
        spec = JobSpec(
            probe="startup_curve",
            params={"n_calls": 4, "warm_threshold_ms": 100.0},
            target_endpoint=base,
            mock_base_url="http://mock",
            mock_token="t",
        )
        run_startup_curve(spec, lambda prog, metrics: seen.append((prog.completed, prog.total)))
    finally:
        srv.shutdown()
    assert (4, 4) in seen
    assert (1, 4) in seen
