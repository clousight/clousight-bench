"""Tests for run_rate_limit and run_concurrency_ceiling data-plane probes."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime.probe.dataplane import run_concurrency_ceiling, run_rate_limit
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


class _Throttling(_FakeAgent):
    reject_after = 15  # 429 once >15 concurrent in flight
    slow_targets = ("prices",)  # probe bodies use target=prices; slow them so bursts overlap
    # Hold each response long enough that a burst of 20 reliably has >15 in flight
    # at once even on a loaded CI runner. 50ms was too thin a margin and flaked
    # (fewer than 16 overlapped → no 429 → "assert False is True").
    slow_ms = 250


def _spec(probe, base, **params):
    return JobSpec(
        probe=probe, params=params, target_endpoint=base, mock_base_url="http://mock", mock_token="t"
    )


def test_rate_limit_detects_429_onset():
    srv, base = _serve(_Throttling)
    try:
        b = run_rate_limit(_spec("rate_limit", base, burst_levels=[10, 20, 40]), lambda p, m: None)
    finally:
        srv.shutdown()
    o = b.observations
    assert o["capability"] == "supported"
    assert o["honors_429"] is True and o["throttle_onset_rps"] in (20.0, 40.0)


def test_rate_limit_reports_no_throttle_against_healthy_fake():
    srv, base = _serve(_FakeAgent)
    try:
        b = run_rate_limit(_spec("rate_limit", base, burst_levels=[10, 20]), lambda p, m: None)
    finally:
        srv.shutdown()
    assert b.observations["throttle_onset_rps"] == 0.0
    assert b.observations["honors_429"] is False


def test_concurrency_ceiling_finds_rejection_level():
    srv, base = _serve(_Throttling)  # reject_after=15
    try:
        b = run_concurrency_ceiling(
            _spec("concurrency_ceiling", base, burst_levels=[10, 50, 100]), lambda p, m: None
        )
    finally:
        srv.shutdown()
    o = b.observations
    assert o["capability"] == "supported"
    assert o["max_in_flight"] in (50, 100) and o["hard_limit"] is True
