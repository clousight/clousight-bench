import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime.probe.invoke import ProbeInvoker
from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec


class _Agent(BaseHTTPRequestHandler):
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
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Agent)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _spec(endpoint):
    return JobSpec(probe="x", params={}, target_endpoint=endpoint,
                   mock_base_url="http://mock", mock_token="t")


def test_one_tool_call_succeeds_against_fake():
    srv, base = _serve()
    try:
        inv = ProbeInvoker(_spec(base))
        ok, ms = inv.one_tool_call()
    finally:
        srv.shutdown()
    assert ok is True and ms >= 0.0


def test_one_tool_call_classified_reports_transport_error_on_bad_endpoint():
    inv = ProbeInvoker(_spec("http://127.0.0.1:1"))  # nothing listening
    ok, ms, err = inv.one_tool_call_classified()
    assert ok is False and err in ("transport", "runtime")


def test_run_tool_plan_records_ttft_and_returns_trace():
    from clousight_bench.domains.agent_runtime.adapters.base import ToolCall
    srv, base = _serve()
    try:
        inv = ProbeInvoker(_spec(base))
        trace = inv.run_tool_plan(inv.create_session(),
                                  [ToolCall(target="prices", params={"p": "aliyun"})])
    finally:
        srv.shutdown()
    assert trace.completed is True and trace.final_state == "completed"
    assert len(trace.attempts) == 1 and trace.attempts[0].ok is True
    assert inv.last_ttft_ms is not None


def test_session_header_scheme_is_honored():
    seen = {}
    class _H(_Agent):
        def do_POST(self):  # noqa: N802
            seen["hdr"] = self.headers.get("X-Custom-Sid")
            _Agent.do_POST(self)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        spec = JobSpec(probe="x", params={}, target_endpoint=base,
                       mock_base_url="http://mock", mock_token="t",
                       session_header_scheme="X-Custom-Sid")
        ProbeInvoker(spec).invoke("sid-123", {"messages": [{"role": "user",
                                  "content": json.dumps({'tool': {}})}]})
    finally:
        srv.shutdown()
    assert seen["hdr"] == "sid-123"
