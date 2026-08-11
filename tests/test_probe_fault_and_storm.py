"""Tests for run_fault_recovery and run_retry_storm data-plane probes."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime.probe.dataplane import run_fault_recovery, run_retry_storm
from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec


class _FakeAgent(BaseHTTPRequestHandler):
    """Minimal AgentRun-compatible target.

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


class _FaultOnThird(_FakeAgent):
    fault_threshold = 3  # succeeds twice, then faults on the 3rd call per session


def _spec(probe, base, **params):
    return JobSpec(
        probe=probe, params=params, target_endpoint=base, mock_base_url="http://mock", mock_token="t"
    )


def test_fault_recovery_records_fault_and_stops():
    srv, base = _serve(_FaultOnThird)
    try:
        b = run_fault_recovery(_spec("fault_recovery", base, fault_call_index=3), lambda p, m: None)
    finally:
        srv.shutdown()
    o = b.observations
    assert o["plan_calls"] == 5
    assert o["completed"] is False and o["final_state"] == "failed"
    assert len(o["attempts"]) == 3 and o["attempts"][-1]["ok"] is False
    assert o["fault"] == {"target": "prices", "fail_on_calls": [3], "status": 500}


def test_retry_storm_aborts_on_first_failure():
    srv, base = _serve(_FaultOnFirst)
    try:
        b = run_retry_storm(_spec("retry_storm", base, max_window_s=5.0, n_calls=5), lambda p, m: None)
    finally:
        srv.shutdown()
    o = b.observations
    assert o["storm_behavior"] == "abort_on_first_failure"
    assert o["calls_attempted"] == 1 and o["duration_ms"] >= 0.0
