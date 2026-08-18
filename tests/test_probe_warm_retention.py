"""Test: run_warm_retention data-plane probe (Task 3)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime.probe.dataplane import run_warm_retention
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
    sleep_by_call: dict = {}  # {per-session call_n: sleep_ms} — models wake latency by call

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
            if cls.sleep_by_call.get(call_n):
                import time as _t

                _t.sleep(cls.sleep_by_call[call_n] / 1000)
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


class _StableWarmAgent(_FakeAgent):
    # Uniform 5ms on every call → a stable warm baseline so every idle probe
    # wakes shallow (5ms is well under the warm_p95×3 tier threshold).
    sleep_by_call = {1: 5.0, 2: 5.0, 3: 5.0, 4: 5.0, 5: 5.0}


def test_warm_retention_stays_shallow_when_latency_stable():
    srv, base = _serve(_StableWarmAgent)  # stable fake → every idle probe wakes shallow
    try:
        spec = JobSpec(
            probe="warm_retention",
            params={"wait_intervals_s": [0.1, 0.1], "warmup_samples": 3},
            target_endpoint=base,
            mock_base_url="http://mock",
            mock_token="t",
        )
        b = run_warm_retention(spec, lambda p, m: None)
    finally:
        srv.shutdown()
    o = b.observations
    assert o["capability"] == "supported"
    assert o["keeps_warm"] is True
    assert o["shallow_retention_s"] == 0.1  # last shallow idle interval
    assert o["deep_onset_s"] is None
    assert o["cold_recycle_s"] is None
    assert o["sweep_capped"] is True  # still shallow at the last (capped) interval
    assert [t["tier"] for t in o["tiers"]] == ["shallow", "shallow"]


class _RecyclingAgent(_FakeAgent):
    # Stable 5ms warm baseline (calls 1-3 warmup, call 4 first idle probe), then
    # escalate: call 5 → 50ms (deep), call 6 → 300ms (cold). The 5ms floor keeps
    # the warm_p95×3 tier threshold above sub-ms timing jitter.
    sleep_by_call = {1: 5.0, 2: 5.0, 3: 5.0, 4: 5.0, 5: 50.0, 6: 300.0}


def test_warm_retention_detects_deep_then_cold_recycle():
    srv, base = _serve(_RecyclingAgent)
    try:
        spec = JobSpec(
            probe="warm_retention",
            # small cold threshold so the 300ms wake reads as a full recycle
            params={
                "wait_intervals_s": [0.1, 0.1, 0.1],
                "warmup_samples": 3,
                "cold_wake_ms": 100.0,
                "deep_wake_factor": 3.0,
            },
            target_endpoint=base,
            mock_base_url="http://mock",
            mock_token="t",
        )
        b = run_warm_retention(spec, lambda p, m: None)
    finally:
        srv.shutdown()
    o = b.observations
    assert o["capability"] == "supported"
    tiers = [t["tier"] for t in o["tiers"]]
    assert tiers == ["shallow", "deep", "cold"]  # escalation captured, breaks at cold
    assert o["deep_onset_s"] == 0.1
    assert o["cold_recycle_s"] == 0.1
    assert o["sweep_capped"] is False  # a real recycle was observed within the sweep
