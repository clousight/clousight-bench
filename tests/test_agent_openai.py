import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime import protocol as p
from clousight_bench.domains.agent_runtime.agent_bundle import agent


class _Tool(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Tool)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_handle_chat_completion_calls_tool():
    srv, base = _serve()
    try:
        body = p.encode_invoke({"target": "prices", "method": "GET"}, base)
        resp = agent.handle_chat_completion(body)
        result = p.decode_result(resp)
        assert result["ok"] is True and result["status"] == 200
        assert result["tool_target"] == "prices"
    finally:
        srv.shutdown()
