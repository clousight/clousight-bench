import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime.probe.dataplane import TTFT_SAMPLES, run_ttft
from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec


def _write_sse(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.end_headers()
    handler.wfile.write(b'data: {"choices":[{"delta":{}}]}\n\n')
    handler.wfile.write(b'data: {"choices":[{"message":{"content":"{\\"ok\\": true}"}}]}\n\n')
    handler.wfile.write(b"data: [DONE]\n\n")
    handler.wfile.flush()


class _SSETarget(BaseHTTPRequestHandler):
    """A fake data-plane endpoint that streams a tiny SSE completion."""

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        _write_sse(self)

    def log_message(self, *a):
        pass


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _SSETarget)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _make_cold_then_warm(cold_delay_s: float):
    """Server whose FIRST response is slow (cold start) then fast (warm)."""
    counter = {"n": 0}

    class _ColdThenWarm(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n)
            counter["n"] += 1
            if counter["n"] == 1:
                time.sleep(cold_delay_s)  # simulate cold start on the first invoke
            _write_sse(self)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _ColdThenWarm)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}", counter


def test_run_ttft_collects_samples_and_reports_progress():
    srv, base = _serve()
    seen = []
    try:
        spec = JobSpec(
            probe="ttft", params={}, target_endpoint=base, mock_base_url="http://mock", mock_token="t"
        )
        bundle = run_ttft(spec, lambda prog, metrics: seen.append((prog.completed, prog.total)))
    finally:
        srv.shutdown()
    assert bundle.observations["capability"] == "supported"
    ttft = bundle.observations["ttft_ms"]
    assert len(ttft) == TTFT_SAMPLES
    assert all(v >= 0.0 for v in ttft)
    assert bundle.series["ttft_ms"][0][0] == 1
    # progress advanced to full during sampling
    assert (TTFT_SAMPLES, TTFT_SAMPLES) in seen


def test_run_ttft_honors_params_sample_count():
    srv, base = _serve()
    seen = []
    try:
        spec = JobSpec(
            probe="ttft",
            params={"warmup": 2, "samples": 3},
            target_endpoint=base,
            mock_base_url="http://mock",
            mock_token="t",
        )
        bundle = run_ttft(spec, lambda prog, metrics: seen.append((prog.completed, prog.total)))
    finally:
        srv.shutdown()
    # spec.params overrides the module-constant default (5).
    assert len(bundle.observations["ttft_ms"]) == 3
    assert (3, 3) in seen


def test_run_ttft_separates_cold_start_from_warm():
    # First invoke is slow (> warm_threshold_ms) → recorded as cold_start_ms;
    # subsequent invokes are fast (< threshold) → the warm steady-state samples.
    srv, base, counter = _make_cold_then_warm(cold_delay_s=0.25)
    try:
        spec = JobSpec(
            probe="ttft",
            params={
                "samples": 4,
                "warm_threshold_ms": 100.0,  # 0.25s cold response exceeds this
                "max_warm_attempts": 4,
            },
            target_endpoint=base,
            mock_base_url="http://mock",
            mock_token="t",
        )
        bundle = run_ttft(spec, lambda prog, metrics: None)
    finally:
        srv.shutdown()
    obs = bundle.observations
    # Cold-start dimension captured the first (slow) invoke.
    assert obs["cold_start_ms"] is not None
    assert obs["cold_start_ms"] >= 250.0
    # Warm dimension collected fast samples, all under the threshold.
    assert obs["warm_samples"] == 4
    assert all(v < 100.0 for v in obs["ttft_ms"])
    assert obs["warm_reliable"] is True
    assert obs["requested_samples"] == 4


def test_run_ttft_warm_unreliable_when_never_warms():
    # Every response stays slow → nothing drops below the threshold, so no warm
    # samples are collected and warm_reliable is False (but the probe still returns).
    srv, base, _ = _make_cold_then_warm(cold_delay_s=0.05)
    try:
        spec = JobSpec(
            probe="ttft",
            params={
                "samples": 3,
                "warm_threshold_ms": 1.0,  # even a 1ms local response exceeds this
                "max_warm_attempts": 2,
                "sample_retries": 1,
            },
            target_endpoint=base,
            mock_base_url="http://mock",
            mock_token="t",
        )
        bundle = run_ttft(spec, lambda prog, metrics: None)
    finally:
        srv.shutdown()
    obs = bundle.observations
    assert obs["warm_samples"] == 0
    assert obs["warm_reliable"] is False
    assert obs["ttft_ms"] == []
