"""T1.12 head-of-line blocking redesign — two-phase baseline-vs-under-slow tests.

Tests cover:
  (a) Phase A baseline measurement: N concurrent fast requests, no slow, measure fast_p50_baseline
  (b) Phase B under-slow measurement: 1 slow (real latency injected) + N fast concurrent, same session
  (c) hol_ratio = under_slow / baseline; serialized = under_slow > baseline * 2.0
  (d) local-sim e2e: T1.12 via orchestrator produces new shape keys
  (e) serialized=False for ThreadingHTTPServer (parallel by nature)

The fake agent server mimics a parallel HTTP server (ThreadingHTTPServer).
Mock latency is injected via POST /latency/config with corr-bucket isolation.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any  # noqa: F401

# ---------------------------------------------------------------------------
# Fake agent server (mimics ThreadingHTTPServer agent — parallel, no HOL)
# ---------------------------------------------------------------------------


class _MockLatencyState:
    """In-process stand-in for the real mock tool server's /latency/config."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latency: dict[str, Any] | None = None
        self._call_counts: dict[str, int] = {}

    def configure_latency(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._latency = payload

    def reset(self) -> None:
        with self._lock:
            self._latency = None
            self._call_counts.clear()

    def next_call_index(self, target: str, corr: str | None) -> int:
        key = f"{target}|{corr}" if corr else target
        with self._lock:
            self._call_counts[key] = self._call_counts.get(key, 0) + 1
            return self._call_counts[key]

    def latency_for(self, target: str, corr: str | None) -> int:
        """Return ms to sleep before serving, or 0."""
        with self._lock:
            lat = self._latency
        if not lat or lat.get("target") != target:
            return 0
        lat_corr = lat.get("corr")
        if lat_corr is not None and lat_corr != corr:
            return 0
        return int(lat.get("add_ms", 0))


def _make_fake_mock_handler(state: _MockLatencyState) -> type[BaseHTTPRequestHandler]:
    """HTTP handler mimicking /prices, /reports, /latency/config."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            from urllib.parse import urlparse

            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send({"ok": True})
                return
            corr = self.headers.get("X-Clousight-Correlation-Id") or None
            if parsed.path in ("/prices", "/reports"):
                target = parsed.path.lstrip("/")
                state.next_call_index(target, corr)
                delay = state.latency_for(target, corr)
                if delay:
                    time.sleep(delay / 1000)
                self._send({"ok": True, "target": target})
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
            if parsed.path == "/latency/config":
                state.configure_latency(payload)
                self._send({"ok": True})
                return
            if parsed.path == "/reports":
                corr = self.headers.get("X-Clousight-Correlation-Id") or None
                target = "reports"
                state.next_call_index(target, corr)
                delay = state.latency_for(target, corr)
                if delay:
                    time.sleep(delay / 1000)
                self._send({"ok": True})
                return
            if parsed.path == "/reset":
                state.reset()
                self._send({"ok": True})
                return
            self._send({"error": "not_found"}, 404)

        def _send(self, body: Any, status: int = 200) -> None:
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
    """Fake parallel agent: receives OpenAI-style invoke, calls mock tool, returns immediately."""

    class Handler(BaseHTTPRequestHandler):
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
            method = (tool.get("method") or "GET").upper()

            import urllib.request

            url = f"{base.rstrip('/')}/{target}"
            req = urllib.request.Request(url, method=method)
            if corr:
                req.add_header("X-Clousight-Correlation-Id", corr)
            if method == "POST":
                req.add_header("Content-Type", "application/json")
                req.data = b"{}"

            ok = False
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    resp.read()
                    ok = True
            except Exception:
                pass

            result = {"ok": ok, "status": 200 if ok else 500}
            out_content = json.dumps(result)
            out = json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": out_content}}]}
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


def _make_spec(target_endpoint: str, mock_base_url: str, **params):
    from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec

    return JobSpec(
        probe="hol_blocking",
        params=params,
        target_endpoint=target_endpoint,
        mock_base_url=mock_base_url,
        mock_token="",
    )


# ---------------------------------------------------------------------------
# Test (a): Phase A baseline: new shape keys present
# ---------------------------------------------------------------------------


def test_hol_blocking_new_shape_keys_present():
    """run_hol_blocking must return the new shape: capability, fast_p50_baseline,
    fast_p50_under_slow, hol_ratio, serialized."""
    latency_state = _MockLatencyState()
    mock_handler = _make_fake_mock_handler(latency_state)
    mock_srv, mock_base = _start_server(mock_handler)

    agent_handler = _make_fake_agent_handler(mock_base)
    agent_srv, agent_base = _start_server(agent_handler)

    try:
        from clousight_bench.domains.agent_runtime.probe.dataplane import run_hol_blocking

        spec = _make_spec(agent_base, mock_base, fast_count=4, slow_latency_ms=50)
        bundle = run_hol_blocking(spec, lambda p, m: None)
    finally:
        mock_srv.shutdown()
        agent_srv.shutdown()

    o = bundle.observations
    assert "capability" in o, f"missing 'capability': {list(o)}"
    assert o["capability"] == "supported"
    assert "fast_p50_baseline" in o, f"missing 'fast_p50_baseline': {list(o)}"
    assert "fast_p50_under_slow" in o, f"missing 'fast_p50_under_slow': {list(o)}"
    assert "hol_ratio" in o, f"missing 'hol_ratio': {list(o)}"
    assert "serialized" in o, f"missing 'serialized': {list(o)}"
    assert isinstance(o["fast_p50_baseline"], float)
    assert isinstance(o["fast_p50_under_slow"], float)
    assert isinstance(o["hol_ratio"], float)
    assert isinstance(o["serialized"], bool)


# ---------------------------------------------------------------------------
# Test (b): ThreadingHTTPServer parallel agent → serialized=False
# ---------------------------------------------------------------------------


def test_hol_blocking_parallel_agent_gives_serialized_false():
    """ThreadingHTTPServer processes requests in parallel → serialized must be False.

    The local mock server is parallel by design, so fast requests during Phase B
    (with 1 slow reports request injecting latency) should NOT be significantly
    delayed. hol_ratio ≈ 1.0, serialized=False.
    """
    latency_state = _MockLatencyState()
    mock_handler = _make_fake_mock_handler(latency_state)
    mock_srv, mock_base = _start_server(mock_handler)

    agent_handler = _make_fake_agent_handler(mock_base)
    agent_srv, agent_base = _start_server(agent_handler)

    try:
        from clousight_bench.domains.agent_runtime.probe.dataplane import run_hol_blocking

        spec = _make_spec(agent_base, mock_base, fast_count=4, slow_latency_ms=150)
        bundle = run_hol_blocking(spec, lambda p, m: None)
    finally:
        mock_srv.shutdown()
        agent_srv.shutdown()

    o = bundle.observations
    # Parallel agent → fast requests NOT blocked by slow one → serialized=False
    assert o["serialized"] is False, (
        f"parallel agent must not serialize: fast_p50_baseline={o['fast_p50_baseline']:.1f}ms "
        f"fast_p50_under_slow={o['fast_p50_under_slow']:.1f}ms hol_ratio={o['hol_ratio']:.3f}"
    )
    # under_slow / baseline ratio must not indicate 2× blocking
    assert o["hol_ratio"] < 2.0, f"hol_ratio={o['hol_ratio']:.3f} should be < 2.0 for parallel server"


# ---------------------------------------------------------------------------
# Test (c): Phase A and B both present; fast_p50_baseline from phase A only
# ---------------------------------------------------------------------------


def test_hol_blocking_baseline_measured_without_slow_injection():
    """Phase A measures fast_p50_baseline with NO latency configured.
    The baseline should reflect pure fast-request latency with no slow interference.
    """
    latency_state = _MockLatencyState()
    mock_handler = _make_fake_mock_handler(latency_state)
    mock_srv, mock_base = _start_server(mock_handler)

    agent_handler = _make_fake_agent_handler(mock_base)
    agent_srv, agent_base = _start_server(agent_handler)

    try:
        from clousight_bench.domains.agent_runtime.probe.dataplane import run_hol_blocking

        spec = _make_spec(agent_base, mock_base, fast_count=4, slow_latency_ms=200)
        bundle = run_hol_blocking(spec, lambda p, m: None)
    finally:
        mock_srv.shutdown()
        agent_srv.shutdown()

    o = bundle.observations
    # Both phases must produce positive floats
    assert o["fast_p50_baseline"] > 0, "baseline must be measured > 0ms"
    assert o["fast_p50_under_slow"] > 0, "under-slow must be measured > 0ms"
    # hol_ratio = under_slow / baseline
    expected_ratio = o["fast_p50_under_slow"] / o["fast_p50_baseline"] if o["fast_p50_baseline"] > 0 else 0.0
    assert abs(o["hol_ratio"] - expected_ratio) < 0.1, (
        f"hol_ratio={o['hol_ratio']:.4f} should ≈ under_slow/baseline={expected_ratio:.4f}"
    )


# ---------------------------------------------------------------------------
# Test (d): local-sim e2e via orchestrator → new shape measurements present
# ---------------------------------------------------------------------------


def test_local_sim_t1_12_orchestrator_produces_new_shape(tmp_path):
    """Running T1.12 on local-sim produces the new measurement keys."""
    from clousight_bench.core.orchestrator import execute
    from clousight_bench.core.schema import RunSpec

    spec = RunSpec("agent-runtime", "T1.12", "local-sim")
    rec = execute(spec, results_dir=tmp_path)

    measurements = rec.measurements
    assert "fast_p50_baseline" in measurements, f"missing 'fast_p50_baseline' in {list(measurements)}"
    assert "fast_p50_under_slow" in measurements, f"missing 'fast_p50_under_slow' in {list(measurements)}"
    assert "hol_ratio" in measurements, f"missing 'hol_ratio' in {list(measurements)}"
    assert "serialized" in measurements, f"missing 'serialized' in {list(measurements)}"
    # local-sim uses ThreadingHTTPServer → serialized=False
    assert measurements["serialized"]["value"] is False


# ---------------------------------------------------------------------------
# Test (e): _pack_hol_blocking in dataplane_dispatch produces new shape
# ---------------------------------------------------------------------------


def test_pack_hol_blocking_produces_new_shape():
    """dataplane_dispatch._pack_hol_blocking must produce the new shape."""
    from clousight_bench.domains.agent_runtime.adapters.base import (
        AgentRuntimeAdapter,
        HOLResult,
    )
    from clousight_bench.domains.agent_runtime.dataplane_dispatch import run_data_plane_probe

    class _FakeAdapter(AgentRuntimeAdapter):
        def create_session(self, spec=None):
            return "s"

        def run_tool_plan(self, session_id, plan):
            raise NotImplementedError

        def destroy_session(self, session_id):
            pass

        def probe_hol_blocking(self) -> HOLResult:
            return HOLResult(
                serialized=False,
                fast_p50_baseline=5.0,
                fast_p50_under_slow=6.0,
                hol_ratio=1.2,
            )

    bundle = run_data_plane_probe(_FakeAdapter(), "hol_blocking", {})
    o = bundle.observations
    assert o["capability"] == "supported"
    assert isinstance(o["fast_p50_baseline"], float)
    assert isinstance(o["fast_p50_under_slow"], float)
    assert isinstance(o["hol_ratio"], float)
    assert isinstance(o["serialized"], bool)
    # Old keys must be gone
    assert "blocked" not in o, "'blocked' is old shape — must be removed"
    assert "fast_p50_ms" not in o, "'fast_p50_ms' is old shape — must be removed"
    assert "slow_p50_ms" not in o, "'slow_p50_ms' is old shape — must be removed"
