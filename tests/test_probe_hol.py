"""Tests for run_hol_blocking data-plane probe (two-phase redesign).

The fake agent server uses ThreadingHTTPServer so it is parallel by design.
Phase A (baseline) and Phase B (under-slow with injected latency) both run.
Expected result: serialized=False, fast_p50_baseline and fast_p50_under_slow
both measured and positive.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime.probe.dataplane import run_hol_blocking
from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec


class _FakeAgent(BaseHTTPRequestHandler):
    """Minimal AgentRun-compatible target — parallel (ThreadingHTTPServer).

    Reads the tool payload from the OpenAI body's user message and calls the
    mock base URL. The mock base URL is embedded in the payload so the agent
    can forward the corr header from ``_correlation_id``.
    """

    _lock = threading.Lock()

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            payload = json.loads(body["messages"][0]["content"])
        except Exception:
            payload = {}

        tool = payload.get("tool") or {}
        base = payload.get("mock_base_url", "")
        corr = payload.get("_correlation_id", "")
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
        content = json.dumps(result)
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class _FakeMockTool(BaseHTTPRequestHandler):
    """Minimal mock tool: /prices (fast), /reports (may sleep), /latency/config."""

    _lock = threading.Lock()
    _latency: dict = {}

    def _latency_ms(self, target: str, corr: str | None) -> int:
        with self._lock:
            lat = type(self)._latency
        if not lat or lat.get("target") != target:
            return 0
        lat_corr = lat.get("corr")
        if lat_corr is not None and lat_corr != corr:
            return 0
        return int(lat.get("add_ms", 0))

    def do_GET(self):  # noqa: N802
        corr = self.headers.get("X-Clousight-Correlation-Id") or None
        if "/prices" in self.path:
            target = "prices"
        elif "/reports" in self.path:
            target = "reports"
        else:
            self._send({"ok": True})
            return
        delay = self._latency_ms(target, corr)
        if delay:
            time.sleep(delay / 1000)
        self._send({"ok": True, "target": target})

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        if "/latency/config" in self.path:
            try:
                cfg = json.loads(raw.decode())
            except Exception:
                cfg = {}
            with self._lock:
                type(self)._latency = cfg
            self._send({"ok": True})
            return
        corr = self.headers.get("X-Clousight-Correlation-Id") or None
        target = "reports"
        delay = self._latency_ms(target, corr)
        if delay:
            time.sleep(delay / 1000)
        self._send({"ok": True})

    def _send(self, body, status=200):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def _serve(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_hol_blocking_new_shape_present():
    """run_hol_blocking returns new two-phase shape: capability, fast_p50_baseline,
    fast_p50_under_slow, hol_ratio, serialized. Old keys blocked/fast_p50_ms/slow_p50_ms absent."""
    _FakeMockTool._latency = {}
    mock_srv, mock_base = _serve(_FakeMockTool)
    agent_srv, agent_base = _serve(_FakeAgent)
    try:
        spec = JobSpec(
            probe="hol_blocking",
            params={"fast_count": 4, "slow_latency_ms": 50},
            target_endpoint=agent_base,
            mock_base_url=mock_base,
            mock_token="",
        )
        b = run_hol_blocking(spec, lambda p, m: None)
    finally:
        _FakeMockTool._latency = {}
        mock_srv.shutdown()
        agent_srv.shutdown()

    o = b.observations
    assert o["capability"] == "supported"
    assert "fast_p50_baseline" in o
    assert "fast_p50_under_slow" in o
    assert "hol_ratio" in o
    assert "serialized" in o
    assert isinstance(o["serialized"], bool)
    # Old keys must be absent
    assert "blocked" not in o
    assert "fast_p50_ms" not in o
    assert "slow_p50_ms" not in o


def test_hol_blocking_serialized_false_for_parallel_server():
    """ThreadingHTTPServer processes requests in parallel → serialized must be False."""
    _FakeMockTool._latency = {}
    mock_srv, mock_base = _serve(_FakeMockTool)
    agent_srv, agent_base = _serve(_FakeAgent)
    try:
        spec = JobSpec(
            probe="hol_blocking",
            params={"fast_count": 4, "slow_latency_ms": 150},
            target_endpoint=agent_base,
            mock_base_url=mock_base,
            mock_token="",
        )
        b = run_hol_blocking(spec, lambda p, m: None)
    finally:
        _FakeMockTool._latency = {}
        mock_srv.shutdown()
        agent_srv.shutdown()

    o = b.observations
    assert o["serialized"] is False, (
        f"parallel server must give serialized=False: "
        f"baseline={o['fast_p50_baseline']:.1f}ms "
        f"under_slow={o['fast_p50_under_slow']:.1f}ms "
        f"ratio={o['hol_ratio']:.3f}"
    )
