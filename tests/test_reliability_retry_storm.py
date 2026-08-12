"""T1.10 retry_storm redesign — mock-counted total attempts + storm-bounded-by tests.

Tests cover:
  (a) all calls fail → total_attempts==3, storm_bounded_by=="agent"
  (b) invoke raises Timeout → storm_bounded_by=="platform"
  (c) local-sim end-to-end: T1.10 runs via orchestrator, new measurement keys present
  (d) storm_bounded_by=="none" when total_attempts > 3 (scorer test)

The fake agent server in (a) implements the lc_agent retry contract:
  5xx → retry up to 2 more times (3 total attempts), 4xx/599 → no retry.
  It keeps per-correlation call_counts in memory and respects fault configs
  keyed by correlation id, matching the mock_tools corr-bucket API.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Fake mock-tool server + fake agent server fixtures (reuse pattern from T1.3)
# ---------------------------------------------------------------------------


class _MockToolState:
    """In-process stand-in for the real mock tool server's /fault/config + /fault/state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fault: dict | None = None
        self._call_counts: dict[str, int] = {}

    def configure_fault(self, payload: dict) -> None:
        with self._lock:
            self._fault = payload
            self._call_counts.clear()

    def reset(self) -> None:
        with self._lock:
            self._fault = None
            self._call_counts.clear()

    def get_state(self) -> dict:
        with self._lock:
            return {"call_counts": dict(self._call_counts), "fault": self._fault}

    def next_call_index(self, target: str, corr: str | None) -> int:
        key = f"{target}|{corr}" if corr else target
        with self._lock:
            self._call_counts[key] = self._call_counts.get(key, 0) + 1
            return self._call_counts[key]

    def should_fault(self, target: str, call_index: int, corr: str | None) -> bool:
        with self._lock:
            f = self._fault
        # Mirror the real mock (mock_tools.fault_status_for): a fault applies only
        # when its "target" matches exactly. An absent target does NOT match all —
        # the probe MUST scope its fault to a target, or it injects nothing. (The
        # old match-all-on-absent-target shortcut hid a real probe bug where the
        # retry-storm fault_config omitted "target" and thus never fired live.)
        if not f or f.get("target") != target:
            return False
        fault_corr = f.get("corr")
        if fault_corr is not None and fault_corr != corr:
            return False
        # fail_from_call + fail_count style (T1.10 uses this)
        fail_from = f.get("fail_from_call")
        fail_count = f.get("fail_count", 0)
        if fail_from is not None:
            return call_index >= fail_from and call_index < fail_from + fail_count
        # fail_on_calls style (T1.3 uses this)
        fail_on = f.get("fail_on_calls")
        if fail_on is not None:
            return call_index in set(fail_on)
        return False


def _make_fake_mock_handler(state: _MockToolState) -> type[BaseHTTPRequestHandler]:
    """HTTP handler that mimics /prices, /fault/config, /fault/state, /reset."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            from urllib.parse import urlparse

            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send({"ok": True})
                return
            if parsed.path == "/fault/state":
                self._send(state.get_state())
                return
            if parsed.path == "/prices":
                corr = self.headers.get("X-Clousight-Correlation-Id") or None
                idx = state.next_call_index("prices", corr)
                if state.should_fault("prices", idx, corr):
                    self._send({"error": "injected_fault"}, 500)
                    return
                self._send({"products": []})
                return
            self._send({"error": "not_found"}, 404)

        def do_POST(self):  # noqa: N802
            from urllib.parse import urlparse

            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode())
            except json.JSONDecodeError:
                self._send({"error": "invalid_json"}, 400)
                return
            if parsed.path == "/fault/config":
                state.configure_fault(payload)
                self._send({"ok": True})
                return
            if parsed.path == "/reset":
                state.reset()
                self._send({"ok": True})
                return
            self._send({"error": "not_found"}, 404)

        def _send(self, body, status: int = 200) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    return Handler


def _make_fake_agent_handler(mock_tool_base: str) -> type[BaseHTTPRequestHandler]:
    """Fake agent that:
    - Receives OpenAI-style invoke
    - Extracts tool, mock_base_url, _correlation_id from payload
    - Calls the mock tool server at mock_base_url with corr header
    - Retries on 5xx up to 2 times (3 total) — the lc_agent contract
    - Returns OpenAI-style result with ok and _tool_http_status
    """

    class Handler(BaseHTTPRequestHandler):
        MAX_ATTEMPTS = 3  # lc_agent retry contract: 3 total (1 + 2 retries)

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode())
            messages = body.get("messages") or []
            content = ""
            for m in messages:
                if m.get("role") == "user":
                    content = m.get("content", "")
                    break
            try:
                payload = json.loads(content)
            except Exception:
                payload = {}

            tool = payload.get("tool") or {}
            base = payload.get("mock_base_url") or mock_tool_base
            corr = payload.get("_correlation_id") or ""
            target = tool.get("target", "prices")

            last_status = 0
            ok = False
            import urllib.request

            for attempt in range(1, self.MAX_ATTEMPTS + 1):
                url = f"{base.rstrip('/')}/{target}"
                req = urllib.request.Request(url)
                if corr:
                    req.add_header("X-Clousight-Correlation-Id", corr)
                try:
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        last_status = resp.status
                        resp.read()
                        ok = True
                        break
                except Exception as exc:
                    last_status = getattr(exc, "code", 599)
                    # 4xx and 599 → no retry
                    if last_status < 500 or last_status == 599:
                        break
                    # 5xx → retry if attempts remain

            result = {"ok": ok, "_tool_http_status": last_status, "status": last_status if not ok else 200}
            content_out = json.dumps(result)
            out = json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": content_out}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    return Handler


def _start_server(handler_cls: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ---------------------------------------------------------------------------
# Helper: build a minimal JobSpec for the probe path
# ---------------------------------------------------------------------------


def _make_spec(target_endpoint: str, mock_base_url: str, **params):
    from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec

    return JobSpec(
        probe="retry_storm",
        params=params,
        target_endpoint=target_endpoint,
        mock_base_url=mock_base_url,
        mock_token="",
    )


# ---------------------------------------------------------------------------
# Test (a): all calls fail → total_attempts==3, storm_bounded_by=="agent"
# ---------------------------------------------------------------------------


def test_retry_storm_all_fail_agent_bounded():
    """Mock all calls failing → agent makes 3 attempts (contract) → storm_bounded_by=='agent'.

    The probe sets fail_from_call:1, fail_count:999 scoped to its own corr bucket.
    The fake agent makes 3 failing attempts (lc_agent contract) then returns ok=False.
    total_attempts=3, storm_bounded_by="agent" (within contract limit).
    """
    tool_state = _MockToolState()
    mock_handler = _make_fake_mock_handler(tool_state)
    mock_srv, mock_base = _start_server(mock_handler)

    agent_handler = _make_fake_agent_handler(mock_base)
    agent_srv, agent_base = _start_server(agent_handler)

    try:
        from clousight_bench.domains.agent_runtime.probe.dataplane import run_retry_storm

        spec = _make_spec(agent_base, mock_base, max_window_s=10.0)
        bundle = run_retry_storm(spec, lambda p, m: None)
    finally:
        mock_srv.shutdown()
        agent_srv.shutdown()

    o = bundle.observations
    assert o["capability"] == "supported"
    assert o["total_attempts"] == 3, f"expected 3 total attempts, got {o['total_attempts']}"
    assert o["storm_bounded_by"] == "agent", f"expected 'agent', got {o['storm_bounded_by']}"
    assert isinstance(o["duration_ms"], float) and o["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# Test (b): invoke raises Timeout → storm_bounded_by=="platform"
# ---------------------------------------------------------------------------


def test_retry_storm_platform_timeout():
    """Monkeypatch inv.invoke to raise Timeout → storm_bounded_by=='platform'."""
    import requests

    tool_state = _MockToolState()
    mock_handler = _make_fake_mock_handler(tool_state)
    mock_srv, mock_base = _start_server(mock_handler)

    try:
        from clousight_bench.domains.agent_runtime.probe.dataplane import run_retry_storm
        from clousight_bench.domains.agent_runtime.probe.invoke import ProbeInvoker

        spec = _make_spec("http://127.0.0.1:19998", mock_base, max_window_s=5.0)

        def _raise_timeout(self, session_id, body):
            raise requests.exceptions.Timeout("simulated timeout")

        with patch.object(ProbeInvoker, "invoke", _raise_timeout):
            bundle = run_retry_storm(spec, lambda p, m: None)
    finally:
        mock_srv.shutdown()

    o = bundle.observations
    assert o["capability"] == "supported"
    assert o["storm_bounded_by"] == "platform", f"expected 'platform', got {o['storm_bounded_by']}"
    assert isinstance(o["duration_ms"], float)


# ---------------------------------------------------------------------------
# Test (c): local-sim end-to-end via orchestrator
# ---------------------------------------------------------------------------


def test_retry_storm_local_sim_e2e(tmp_path):
    """Running T1.10 on local-sim produces total_attempts, storm_bounded_by measurements."""
    from clousight_bench.core.orchestrator import execute
    from clousight_bench.core.schema import RunSpec

    spec = RunSpec("agent-runtime", "T1.10", "local-sim")
    rec = execute(spec, results_dir=tmp_path)

    measurements = rec.measurements
    # New shape: these keys must be present
    assert "total_attempts" in measurements, f"missing 'total_attempts' in {list(measurements)}"
    assert "storm_bounded_by" in measurements, f"missing 'storm_bounded_by' in {list(measurements)}"
    assert "retry_storm_capability" in measurements, (
        f"missing 'retry_storm_capability' in {list(measurements)}"
    )
    assert "duration_ms" in measurements, f"missing 'duration_ms' in {list(measurements)}"
    # Local-sim deterministic: total_attempts=3, storm_bounded_by="agent"
    assert measurements["total_attempts"]["value"] == 3
    assert measurements["storm_bounded_by"]["value"] == "agent"


# ---------------------------------------------------------------------------
# Test (d): storm_bounded_by="none" scored as error (scorer unit test)
# ---------------------------------------------------------------------------


def test_retry_storm_score_none_is_error():
    """score() with storm_bounded_by='none' → error finding '无界重试风暴风险'."""
    from clousight_bench.core.observation import ObservationBundle
    from clousight_bench.domains.agent_runtime.tasks.t1_10_retry_storm import RetryStormTask

    obs = ObservationBundle(
        observations={
            "capability": "supported",
            "total_attempts": 10,
            "storm_bounded_by": "none",
            "duration_ms": 29999.0,
        }
    )
    result = RetryStormTask().score(obs)
    codes = [f.code for f in result.findings]
    assert "agent_runtime.retry_storm_unbounded" in codes
    # Check severity is critical (framework maps "error" → "critical")
    unbounded = next(f for f in result.findings if f.code == "agent_runtime.retry_storm_unbounded")
    assert unbounded.severity == "critical"
    # Check Chinese message is present
    assert "无界重试风暴风险" in unbounded.summary


def test_retry_storm_score_platform_is_info():
    """score() with storm_bounded_by='platform' → info finding."""
    from clousight_bench.core.observation import ObservationBundle
    from clousight_bench.domains.agent_runtime.tasks.t1_10_retry_storm import RetryStormTask

    obs = ObservationBundle(
        observations={
            "capability": "supported",
            "total_attempts": 0,
            "storm_bounded_by": "platform",
            "duration_ms": 30000.0,
        }
    )
    result = RetryStormTask().score(obs)
    codes = [f.code for f in result.findings]
    assert "agent_runtime.retry_storm_platform_bounded" in codes
    platform_f = next(f for f in result.findings if f.code == "agent_runtime.retry_storm_platform_bounded")
    assert platform_f.severity == "info"


def test_retry_storm_score_agent_bounded_no_findings():
    """score() with storm_bounded_by='agent' → no findings."""
    from clousight_bench.core.observation import ObservationBundle
    from clousight_bench.domains.agent_runtime.tasks.t1_10_retry_storm import RetryStormTask

    obs = ObservationBundle(
        observations={
            "capability": "supported",
            "total_attempts": 3,
            "storm_bounded_by": "agent",
            "duration_ms": 50.0,
        }
    )
    result = RetryStormTask().score(obs)
    assert not result.findings
    assert result.measurements["total_attempts"].value == 3
    assert result.measurements["storm_bounded_by"].value == "agent"
    assert result.measurements["retry_storm_capability"].value == "supported"
    assert result.measurements["duration_ms"].evidence == "B"


# ---------------------------------------------------------------------------
# Dispatch layer: _pack_retry_storm uses new shape
# ---------------------------------------------------------------------------


def test_pack_retry_storm_produces_new_shape():
    """dataplane_dispatch._pack_retry_storm must produce the new shape."""
    from clousight_bench.domains.agent_runtime.adapters.base import (
        AgentRuntimeAdapter,
        RetryStormResult,
    )
    from clousight_bench.domains.agent_runtime.dataplane_dispatch import run_data_plane_probe

    class _FakeAdapter(AgentRuntimeAdapter):
        def create_session(self, spec=None):
            return "s"

        def run_tool_plan(self, session_id, plan):
            raise NotImplementedError

        def destroy_session(self, session_id):
            pass

        def probe_retry_storm(self, max_window_s: float = 30.0):
            return RetryStormResult(
                capability="supported",
                total_attempts=3,
                storm_bounded_by="agent",
                duration_ms=50.0,
            )

    bundle = run_data_plane_probe(_FakeAdapter(), "retry_storm", {})
    o = bundle.observations
    assert o["capability"] == "supported"
    assert o["total_attempts"] == 3
    assert o["storm_bounded_by"] == "agent"
    assert o["duration_ms"] == 50.0
