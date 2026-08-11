"""Tests for run_fault_recovery and run_retry_storm data-plane probes."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime.probe.dataplane import run_fault_recovery, run_retry_storm
from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec


class _FakeAgent(BaseHTTPRequestHandler):
    """Minimal AgentRun-compatible target for retry_storm tests.

    Reads the tool payload from the OpenAI body's user message, honors
    ``fail_after_n_calls`` per session header, models a slower ``reports``
    target, and returns 429 past a burst threshold. Subclass attributes tune it.
    """

    fault_threshold = 0  # >0: return ok=false with _fault_injected on the Nth+ call per session
    reject_after = 0  # >0: return HTTP 429 once this many concurrent calls seen
    slow_targets = ()  # tool targets that sleep slow_ms
    slow_ms = 0

    _counts: dict = {}
    _inflight = [0]
    _lock = threading.Lock()

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        payload = json.loads(body["messages"][0]["content"])
        tool = payload.get("tool") or {}
        sid = self.headers.get("X-AgentRun-Session-ID", "")
        cls = type(self)
        with cls._lock:
            cls._inflight[0] += 1
            inflight = cls._inflight[0]
            cls._counts[sid] = cls._counts.get(sid, 0) + 1
            call_n = cls._counts[sid]
        try:
            if cls.reject_after and inflight > cls.reject_after:
                self.send_response(429)
                self.send_header("Retry-After", "1")
                self.end_headers()
                return
            if cls.slow_ms and tool.get("target") in cls.slow_targets:
                import time as _t

                _t.sleep(cls.slow_ms / 1000)
            faulted = bool(cls.fault_threshold and call_n >= cls.fault_threshold)
            result = {"ok": not faulted, "status": 500 if faulted else 200}
            if faulted:
                result["_fault_injected"] = True
            content = json.dumps(result)
            out = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
        finally:
            with cls._lock:
                cls._inflight[0] -= 1

    def log_message(self, *a):
        pass


def _serve(handler_cls):
    handler_cls._counts = {}
    handler_cls._inflight = [0]
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class _FaultOnFirst(_FakeAgent):
    fault_threshold = 1  # every call fails (ok=false, _fault_injected)


def _spec(probe, base, **params):
    return JobSpec(
        probe=probe, params=params, target_endpoint=base, mock_base_url="http://mock", mock_token="t"
    )


def test_fault_recovery_produces_new_shape():
    """run_fault_recovery returns the new three-state shape: capability, recovered, observed_attempts."""
    # Use a minimal mock server that accepts POST /fault/config and GET /fault/state
    # (the probe configures and reads it), and a fake agent that returns ok=True.
    import json as _json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _MockServer(BaseHTTPRequestHandler):
        _fault = {}
        _call_counts = {}
        _lock = threading.Lock()

        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                self._send({"ok": True})
            elif self.path == "/fault/state":
                self._send({"call_counts": {}, "fault": self._fault})
            else:
                self._send({"products": []})

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length", "0"))
            body = _json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/fault/config":
                type(self)._fault = body
                self._send({"ok": True})
            else:
                self._send({"ok": True})

        def _send(self, data, status=200):
            out = _json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    mock_srv = ThreadingHTTPServer(("127.0.0.1", 0), _MockServer)
    threading.Thread(target=mock_srv.serve_forever, daemon=True).start()
    mock_base = f"http://127.0.0.1:{mock_srv.server_address[1]}"

    # Fake agent: returns ok=True (recovered)
    class _AgentOk(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(n)
            result = {"ok": True, "status": 200}
            content = _json.dumps(result)
            out = _json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    agent_srv = ThreadingHTTPServer(("127.0.0.1", 0), _AgentOk)
    threading.Thread(target=agent_srv.serve_forever, daemon=True).start()
    agent_base = f"http://127.0.0.1:{agent_srv.server_address[1]}"

    try:
        from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec
        spec2 = JobSpec(
            probe="fault_recovery", params={},
            target_endpoint=agent_base, mock_base_url=mock_base, mock_token=""
        )
        b = run_fault_recovery(spec2, lambda p, m: None)
    finally:
        mock_srv.shutdown()
        agent_srv.shutdown()

    o = b.observations
    assert o["capability"] == "supported", f"missing capability, got {list(o)}"
    assert "recovered" in o
    assert "observed_attempts" in o
    assert "recovery_ms" in o
    assert "platform_terminated" in o


def test_retry_storm_aborts_on_first_failure():
    srv, base = _serve(_FaultOnFirst)
    try:
        b = run_retry_storm(_spec("retry_storm", base, max_window_s=5.0, n_calls=5), lambda p, m: None)
    finally:
        srv.shutdown()
    o = b.observations
    assert o["storm_behavior"] == "abort_on_first_failure"
    assert o["calls_attempted"] == 1 and o["duration_ms"] >= 0.0
