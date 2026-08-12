import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime.probe.dataplane import TTFT_SAMPLES, run_ttft
from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec


class _SSETarget(BaseHTTPRequestHandler):
    """A fake data-plane endpoint that streams a tiny SSE completion."""

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'data: {"choices":[{"delta":{}}]}\n\n')
        self.wfile.write(b'data: {"choices":[{"message":{"content":"{\\"ok\\": true}"}}]}\n\n')
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *a):
        pass


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _SSETarget)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


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
