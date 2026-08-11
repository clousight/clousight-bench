import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from clousight_bench.domains.agent_runtime.agent_bundle import agent
from clousight_bench.domains.agent_runtime import protocol as p
from clousight_bench.domains.agent_runtime.aliyun import AliyunAgentRunTransport
from clousight_bench.domains.agent_runtime.adapters.base import ToolCall


class _FakeAdapter:
    def __init__(self, base):
        self.target = {"mock_base_url": base}
        self.run_id = None

    @property
    def mock_base_url(self):
        return self.target["mock_base_url"]


class _Tool(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def _serve_tool():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Tool)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _local_invoke(session_id, body):
    # emulate the deployed agent in-process: OpenAI body -> agent -> OpenAI resp
    return agent.handle_chat_completion(body)


def test_run_tool_plan_end_to_end():
    srv, base = _serve_tool()
    try:
        t = AliyunAgentRunTransport(_FakeAdapter(base))
        t._invoke = _local_invoke
        sid = t.create_session()
        trace = t.run_tool_plan(sid, [ToolCall(target="prices"), ToolCall(target="inventory")])
        assert trace.completed is True and trace.final_state == "completed"
        assert [a.ok for a in trace.attempts] == [True, True]
        assert [a.call_index for a in trace.attempts] == [1, 2]
    finally:
        srv.shutdown()


def test_probe_scaling_against_local_agent():
    srv, base = _serve_tool()
    try:
        t = AliyunAgentRunTransport(_FakeAdapter(base))
        t._invoke = _local_invoke
        points = t.probe_scaling([1, 2, 4])
        assert [pt.concurrency for pt in points] == [1, 2, 4]
        assert all(pt.success_rate == 1.0 for pt in points)
        assert all(pt.p95_ms >= 0 for pt in points)
    finally:
        srv.shutdown()


def test_invoke_seam_default_is_live_gated():
    t = AliyunAgentRunTransport(_FakeAdapter("http://x"))
    with pytest.raises(NotImplementedError):
        t.run_tool_plan(t.create_session(), [ToolCall(target="prices")])


def test_run_tool_plan_observes_tool_failure():
    def failing_invoke(session_id, body):
        return p.encode_result({"ok": False, "status": 500, "tool_target": "prices"})

    t = AliyunAgentRunTransport(_FakeAdapter("http://x"))
    t._invoke = failing_invoke
    trace = t.run_tool_plan(t.create_session(), [ToolCall(target="prices")])
    assert trace.completed is False and trace.final_state == "failed"
    assert trace.attempts[0].status == 500 and trace.attempts[0].ok is False
