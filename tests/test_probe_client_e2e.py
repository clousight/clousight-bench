import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime.probe.client import RemoteProbeClient, ProbeJobFailed
from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec
from clousight_bench.domains.agent_runtime.probe.runner import JobRunner
from clousight_bench.domains.agent_runtime.probe.server import serve, build_default_runner
from clousight_bench.domains.agent_runtime.probe.dataplane import TTFT_SAMPLES
from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime.probe.jobs import JobProgress


class _SSETarget(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'data: {"choices":[{"delta":{}}]}\n\n')
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
    def log_message(self, *a):
        pass


def _serve_target():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _SSETarget)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_client_runs_ttft_job_end_to_end():
    probe_srv = serve(build_default_runner(), host="127.0.0.1", port=0)
    target_srv, target = _serve_target()
    seen_progress = []
    try:
        client = RemoteProbeClient(f"http://127.0.0.1:{probe_srv.server_address[1]}",
                                   poll_interval_s=0.02, timeout_s=10)
        spec = JobSpec(probe="ttft", params={}, target_endpoint=target,
                       mock_base_url="http://mock", mock_token="t")
        bundle = client.run_job(spec, on_progress=lambda p, m: seen_progress.append(p.completed))
    finally:
        probe_srv.shutdown()
        target_srv.shutdown()
    assert isinstance(bundle, ObservationBundle)
    assert bundle.observations["capability"] == "supported"
    assert len(bundle.observations["ttft_ms"]) == TTFT_SAMPLES
    assert max(seen_progress) == TTFT_SAMPLES


def test_client_raises_on_failed_job():
    def boom(spec, progress_cb):
        raise RuntimeError("kaboom")
    probe_srv = serve(JobRunner({"boom": boom}), host="127.0.0.1", port=0)
    try:
        client = RemoteProbeClient(f"http://127.0.0.1:{probe_srv.server_address[1]}",
                                   poll_interval_s=0.02, timeout_s=10)
        spec = JobSpec(probe="boom", params={}, target_endpoint="u")
        try:
            client.run_job(spec)
            assert False, "expected ProbeJobFailed"
        except ProbeJobFailed as e:
            assert "kaboom" in str(e)
    finally:
        probe_srv.shutdown()
