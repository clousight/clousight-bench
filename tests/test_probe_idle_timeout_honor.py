"""Test: run_idle_timeout_honor data-plane probe (T1.14).

sessionIdleTimeoutSeconds is a keep-warm PROMISE: within the configured window
the instance must stay hot (honored). Past the window, a decay sweep finds when
it goes deep / cold.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime.probe.dataplane import run_idle_timeout_honor
from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec


class _IdleAgent(BaseHTTPRequestHandler):
    """Fake AgentRun target that sleeps per-session-call to model wake latency."""

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
            # calls 1-3 warmup, call 4 = promise probe, calls 5-6 = decay sweep.
            params={
                "session_idle_timeout_s": 0.1,
                "promise_idle_s": 0.05,
                "decay_intervals_s": [0.05, 0.05],
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
    # Stable 5ms warm baseline; promise probe (call 4) stays warm → honored.
    # Decay: call 5 → 50ms (deep), call 6 → 300ms (cold).
    sleep_by_call = {1: 5.0, 2: 5.0, 3: 5.0, 4: 5.0, 5: 50.0, 6: 300.0}


class _PromiseBrokenAgent(_IdleAgent):
    # Instance already cold WITHIN the promise window (call 4 slow) → not honored.
    sleep_by_call = {1: 5.0, 2: 5.0, 3: 5.0, 4: 300.0, 5: 300.0}


def test_promise_honored_and_decay_curve_captured():
    o = _run(_HonoringAgent)
    assert o["capability"] == "supported"
    assert o["configured_idle_s"] == 0.1
    assert o["promise_tier"] == "shallow"  # warm within the promised window
    assert o["honored"] is True
    # post-promise decay: deep at first step, cold at second (breaks there)
    assert o["deep_onset_s"] == 0.05
    assert o["cold_onset_s"] == 0.05
    assert o["decay_capped"] is False
    assert [t["tier"] for t in o["decay_tiers"]] == ["deep", "cold"]


def test_promise_broken_when_cold_inside_window():
    o = _run(_PromiseBrokenAgent)
    assert o["capability"] == "supported"
    assert o["promise_tier"] == "cold"  # cold while still inside the promise
    assert o["honored"] is False
