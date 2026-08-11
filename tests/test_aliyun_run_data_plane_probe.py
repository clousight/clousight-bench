import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.core.observation import ObservationBundle


class _FakeAgent(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        out = json.dumps({"choices": [{"message": {"role": "assistant",
              "content": json.dumps({"ok": True, "status": 200})}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
    def log_message(self, *a):
        pass


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAgent)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _transport(endpoint):
    # Build a transport in mock/lazy mode with the endpoint + mock config injected
    # via the adapter target, so run_data_plane_probe resolves target_endpoint
    # without provisioning. Follow the pattern in test_dataplane_invoke.py for
    # constructing AliyunAgentRunTransport with an adapter stub; set
    # transport._endpoint_public_url = endpoint directly to skip provision.
    from clousight_bench.domains.agent_runtime.aliyun import AliyunAgentRunTransport

    class _Adapter:
        target = {"mock_token": "t", "region": "cn-hangzhou"}
        mock_base_url = "http://mock"
        run_id = None
    t = AliyunAgentRunTransport(_Adapter())
    t._endpoint_public_url = endpoint
    return t


def test_run_data_plane_probe_soak_in_process_against_fake():
    srv, base = _serve()
    try:
        t = _transport(base)
        b = t.run_data_plane_probe("soak", {"duration_s": 0.3})
    finally:
        srv.shutdown()
    assert isinstance(b, ObservationBundle)
    assert b.observations["capability"] == "supported"
    assert b.observations["availability"] == 1.0
    assert b.observations["vantage"]["carrier"] == "local"
    assert b.observations["vantage"]["region"] == "cn-hangzhou"


def test_run_data_plane_probe_ttft_returns_series():
    srv, base = _serve()
    try:
        t = _transport(base)
        b = t.run_data_plane_probe("ttft", {})
    finally:
        srv.shutdown()
    assert b.observations["capability"] == "supported"
    assert len(b.observations["ttft_ms"]) == 5
    assert "ttft_ms" in b.series
