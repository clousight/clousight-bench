"""Tests for run_rate_limit and run_concurrency_ceiling data-plane probes."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime.probe.dataplane import run_concurrency_ceiling, run_rate_limit
from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec


class _FakeAgent(BaseHTTPRequestHandler):
    """Minimal AgentRun-compatible target.

    Reads the tool payload from the OpenAI body's user message, honors
    ``fail_after_n_calls`` per session header, and returns 429 past a burst
    threshold. Subclass attributes tune it.
    """

    fault_threshold = 0  # >0: return ok=false with _fault_injected on the Nth+ call per session
    reject_after_in_burst = 0  # >0: 429 from the Nth+ request *within one burst level*

    _counts: dict = {}
    _burst_counts: dict = {}
    _lock = threading.Lock()

    def _burst_key(self) -> str | None:
        """The burst this request belongs to, from a ``<probe>-<burst>-<i>`` sid.

        run_rate_limit sends ``rl-<burst>-<i>`` and run_concurrency_ceiling sends
        ``ceil-<burst>-<i>``. Anything else (the warm-up session) is None, so
        warm-up never eats a burst's budget.
        """
        parts = self.headers.get("X-AgentRun-Session-ID", "").rsplit("-", 2)
        if len(parts) != 3 or not (parts[1].isdigit() and parts[2].isdigit()):
            return None
        return f"{parts[0]}-{parts[1]}"

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        # Parsed to assert the probe really sends a well-formed tool payload;
        # the response no longer varies by target.
        json.loads(body["messages"][0]["content"])
        sid = self.headers.get("X-AgentRun-Session-ID", "")
        cls = type(self)
        burst = self._burst_key()
        with cls._lock:
            cls._counts[sid] = cls._counts.get(sid, 0) + 1
            call_n = cls._counts[sid]
            n_in_burst = 0
            if burst is not None:
                cls._burst_counts[burst] = cls._burst_counts.get(burst, 0) + 1
                n_in_burst = cls._burst_counts[burst]
        if cls.reject_after_in_burst and n_in_burst > cls.reject_after_in_burst:
            self.send_response(429)
            self.send_header("Retry-After", "1")
            self.end_headers()
            return
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

    def log_message(self, *a):
        pass


def _serve(handler_cls):
    handler_cls._counts = {}
    handler_cls._burst_counts = {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class _Throttling(_FakeAgent):
    """A target that starts throttling partway through a burst.

    Counted per burst level, NOT by wall-clock overlap. The previous version
    429'd on ">15 requests in flight at once" and manufactured that overlap by
    sleeping in every handler — a race the probe lost whenever the runner was
    loaded, which is why the sleep had already gone 50ms -> 250ms for this exact
    flake ("fewer than 16 overlapped -> no 429 -> assert False is True") and
    then flaked again at 250ms on test (3.13).

    Nothing about these probes needs genuine simultaneity: they issue the burst
    with ThreadPoolExecutor(max_workers=burst_n) and then read nothing but the
    HTTP status codes. Counting reproduces exactly that observable with no
    timing at all, and drops ~5s of sleeps from the suite.
    """

    reject_after_in_burst = 15


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
    # Exact, not `in (20.0, 40.0)`: the 20-burst is the first to exceed
    # reject_after_in_burst, deterministically. The old range only existed to
    # tolerate the race, and would have passed a probe that mis-reported 40.
    assert o["honors_429"] is True and o["throttle_onset_rps"] == 20.0


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
    # Exact for the same reason: 35 of the 50-burst are rejected (70%), well past
    # the 10% rejection_threshold, so 50 is the ceiling every time.
    assert o["max_in_flight"] == 50 and o["hard_limit"] is True
