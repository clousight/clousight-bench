"""Test: run_idle_timeout_honor data-plane probe (T1.14)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime.probe.dataplane import run_idle_timeout_honor
from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec


class _IdleAgent(BaseHTTPRequestHandler):
    """Fake AgentRun target that sleeps per-session-call to model wake latency.

    ``sleep_by_call`` maps the Nth same-session call to a sleep in ms, so we can
    simulate "warm under the timeout, recycled (slow cold rebuild) over it".
    """

    sleep_by_call: dict = {}
    _counts: dict = {}
    _lock = threading.Lock()

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        sid = self.headers.get("X-AgentRun-Session-ID", "")
        cls = type(self)
        with cls._lock:
            cls._counts[sid] = cls._counts.get(sid, 0) + 1
            call_n = cls._counts[sid]
        if cls.sleep_by_call.get(call_n):
            import time as _t

            _t.sleep(cls.sleep_by_call[call_n] / 1000)
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": "{\"ok\": true}"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def _serve(handler_cls):
    handler_cls._counts = {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _run(handler_cls):
    srv, base = _serve(handler_cls)
    try:
        spec = JobSpec(
            probe="idle_timeout_honor",
            # 3 warmup calls, then under-probe (call 4) and over-probe (call 5).
            params={
                "session_idle_timeout_s": 0.1,
                "under_idle_s": 0.05,
                "over_idle_s": 0.05,
                "cold_wake_ms": 100.0,
                "deep_wake_factor": 3.0,
            },
            target_endpoint=base,
            mock_base_url="http://mock",
            mock_token="t",
        )
        return run_idle_timeout_honor(spec, lambda p, m: None).observations
    finally:
        srv.shutdown()


class _HonoringAgent(_IdleAgent):
    # Stable 5ms warm baseline (calls 1-3 warmup, call 4 under-timeout probe);
    # over-timeout probe (call 5) is slow → recycled (cold wake). The 5ms floor
    # keeps the warm_p95×3 tier threshold above sub-ms timing jitter.
    sleep_by_call = {1: 5.0, 2: 5.0, 3: 5.0, 4: 5.0, 5: 300.0}


class _IgnoringAgent(_IdleAgent):
    # stays fast (5ms) even over the timeout → the knob was ignored (not honored).
    sleep_by_call = {1: 5.0, 2: 5.0, 3: 5.0, 4: 5.0, 5: 5.0}


def test_idle_timeout_honored_when_recycled_over_timeout():
    o = _run(_HonoringAgent)
    assert o["capability"] == "supported"
    assert o["configured_idle_s"] == 0.1
    assert o["under_tier"] == "shallow"
    assert o["over_tier"] in ("deep", "cold")
    assert o["honored"] is True


def test_idle_timeout_not_honored_when_stays_warm():
    o = _run(_IgnoringAgent)
    assert o["capability"] == "supported"
    assert o["under_tier"] == "shallow"
    assert o["over_tier"] == "shallow"  # never recycled → knob ignored
    assert o["honored"] is False
