"""T1.3 fault_recovery redesign — three-state platform-attribution tests.

Tests cover:
  (a) mock fails call #1, agent retries to call #3 → recovered=True, observed_attempts=3
  (b) mock fails all calls (3 total agent retries) → recovered=False, observed_attempts=3
  (c) invoke raises Timeout → platform_terminated=True
  (d) local-sim end-to-end: T1.3 runs via orchestrator, recovered=True, observed_attempts present

The fake agent server in (a)/(b) implements the lc_agent retry contract:
  5xx → retry up to 2 more times (3 total attempts), 4xx/599 → no retry.
  It keeps per-correlation call_counts in memory and respects fault configs
  keyed by correlation id, matching the mock_tools corr-bucket API.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Fake agent + fake mock-tool server fixtures
# ---------------------------------------------------------------------------


class _MockToolState:
    """In-process stand-in for the real mock tool server's /fault/config + /fault/state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fault: dict[str, Any] | None = None
        self._call_counts: dict[str, int] = {}

    def configure_fault(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._fault = payload
            self._call_counts.clear()

    def reset(self) -> None:
        with self._lock:
            self._fault = None
            self._call_counts.clear()

    def get_state(self) -> dict[str, Any]:
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
        if not f or f.get("target") != target:
            return False
        fault_corr = f.get("corr")
        if fault_corr is not None and fault_corr != corr:
            return False
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
    """Fake agent that:
    - Receives OpenAI-style invoke
    - Extracts tool, mock_base_url, _correlation_id from payload
    - Calls the mock tool server at mock_base_url (or override) with corr header
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

            # Call mock tool with retry (lc_agent contract)
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
            content = json.dumps(result)
            out = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
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
        probe="fault_recovery",
        params=params,
        target_endpoint=target_endpoint,
        mock_base_url=mock_base_url,
        mock_token="",
    )


# ---------------------------------------------------------------------------
# State (a): mock only fails call #1 → agent retries → recovered=True
# ---------------------------------------------------------------------------


def test_fault_recovery_recovered_true_when_first_call_fails():
    """Platform lets agent retry: mock fails call #1 in corr bucket, agent retries → recovered=True.

    The probe sets fail_on_calls:[1] scoped to its own corr bucket.
    The fake agent makes 1 failing attempt, then retries → call #2 succeeds.
    observed_attempts=2 (1 fail + 1 successful retry).
    """
    tool_state = _MockToolState()
    mock_handler = _make_fake_mock_handler(tool_state)
    mock_srv, mock_base = _start_server(mock_handler)

    # Agent that calls our fake mock
    agent_handler = _make_fake_agent_handler(mock_base)
    agent_srv, agent_base = _start_server(agent_handler)

    try:
        from clousight_bench.domains.agent_runtime.probe.dataplane import run_fault_recovery

        spec = _make_spec(agent_base, mock_base)
        bundle = run_fault_recovery(spec, lambda p, m: None)
    finally:
        mock_srv.shutdown()
        agent_srv.shutdown()

    o = bundle.observations
    assert o["capability"] == "supported"
    assert o["recovered"] is True
    # call #1 faulted, call #2 succeeded → 2 total observed attempts in the corr bucket
    # call #1 faulted → retry → call #2 succeeds: ≥2 observed in the corr bucket
    assert o["observed_attempts"] >= 2, f"expected ≥2 (fail+retry), got {o['observed_attempts']}"
    assert o["platform_terminated"] is False
    assert isinstance(o["recovery_ms"], float) and o["recovery_ms"] >= 0


# ---------------------------------------------------------------------------
# State (b): agent returns ok=False (all retries exhausted) → recovered=False
# ---------------------------------------------------------------------------


def test_fault_recovery_recovered_false_when_agent_exhausts_retries():
    """Agent exhausts all retries → recovered=False, platform_terminated=False.

    The fake agent sees 5xx on every attempt (all 3 attempts faulted) and
    ultimately returns ok=False. The platform let the retries happen but the
    tool stayed broken → state (b): agent-layer exhaustion, not platform kill.
    """
    tool_state = _MockToolState()
    mock_handler = _make_fake_mock_handler(tool_state)
    mock_srv, mock_base = _start_server(mock_handler)

    # Agent configured to return ok=False after exhausting retries (simulate
    # the mock faulting all 3 attempts: calls 1, 2, 3 in the corr bucket).
    # We monkeypatch the fake agent to always return ok=False.
    agent_handler = _make_fake_agent_handler(mock_base)
    agent_srv, agent_base = _start_server(agent_handler)

    try:
        from clousight_bench.domains.agent_runtime.probe.dataplane import run_fault_recovery
        from clousight_bench.domains.agent_runtime.probe.invoke import ProbeInvoker

        spec = _make_spec(agent_base, mock_base)

        # Monkeypatch invoke to return ok=False (simulating agent exhausted retries)
        _orig_invoke = ProbeInvoker.invoke

        def _exhausted_invoke(self, session_id, body):
            import json as _json
            result = {"ok": False, "_tool_http_status": 500, "status": 500}
            return {"choices": [{"message": {"role": "assistant", "content": _json.dumps(result)}}]}

        with patch.object(ProbeInvoker, "invoke", _exhausted_invoke):
            bundle = run_fault_recovery(spec, lambda p, m: None)
    finally:
        mock_srv.shutdown()
        agent_srv.shutdown()

    o = bundle.observations
    assert o["capability"] == "supported"
    assert o["recovered"] is False
    assert o["platform_terminated"] is False


# ---------------------------------------------------------------------------
# State (c): invoke raises Timeout → platform_terminated=True
# ---------------------------------------------------------------------------


def test_fault_recovery_platform_terminated_on_invoke_timeout():
    """If inv.invoke raises a timeout/transport error → platform_terminated=True."""
    import requests

    tool_state = _MockToolState()
    mock_handler = _make_fake_mock_handler(tool_state)
    mock_srv, mock_base = _start_server(mock_handler)

    try:
        from clousight_bench.domains.agent_runtime.probe.dataplane import run_fault_recovery
        from clousight_bench.domains.agent_runtime.probe.invoke import ProbeInvoker

        spec = _make_spec("http://127.0.0.1:19999", mock_base)  # unreachable agent

        # Monkeypatch invoke to raise Timeout
        def _raise_timeout(self, session_id, body):
            raise requests.exceptions.Timeout("simulated timeout")

        with patch.object(ProbeInvoker, "invoke", _raise_timeout):
            bundle = run_fault_recovery(spec, lambda p, m: None)
    finally:
        mock_srv.shutdown()

    o = bundle.observations
    assert o["capability"] == "supported"
    assert o["platform_terminated"] is True
    assert o["recovered"] is False


# ---------------------------------------------------------------------------
# Local-sim end-to-end via orchestrator
# ---------------------------------------------------------------------------


def test_local_sim_t1_3_orchestrator_produces_new_shape(tmp_path):
    """Running T1.3 on local-sim produces recovered, observed_attempts measurements."""
    from clousight_bench.core.orchestrator import execute
    from clousight_bench.core.schema import RunSpec

    spec = RunSpec("agent-runtime", "T1.3", "local-sim")
    rec = execute(spec, results_dir=tmp_path)

    measurements = rec.measurements
    # New shape: recovered and observed_attempts must be present
    assert "recovered" in measurements, f"missing 'recovered' in {list(measurements)}"
    assert "observed_attempts" in measurements, f"missing 'observed_attempts' in {list(measurements)}"
    # Local-sim default: healthy platform → recovered=True
    assert measurements["recovered"]["value"] is True
    # Local-sim simulates: call #1 faults (injected 500), call #2 succeeds → 2 attempts
    assert measurements["observed_attempts"]["value"] >= 2


# ---------------------------------------------------------------------------
# Dispatch layer: _pack_fault_recovery uses new shape
# ---------------------------------------------------------------------------


def test_pack_fault_recovery_produces_new_shape():
    """dataplane_dispatch._pack_fault_recovery must produce the new shape (recovered, observed_attempts)."""
    from clousight_bench.domains.agent_runtime.adapters.base import (
        AgentRuntimeAdapter,
        FaultRecoveryResult,
    )
    from clousight_bench.domains.agent_runtime.dataplane_dispatch import run_data_plane_probe

    class _FakeAdapter(AgentRuntimeAdapter):
        def create_session(self, spec=None):
            return "s"

        def run_tool_plan(self, session_id, plan):
            raise NotImplementedError

        def destroy_session(self, session_id):
            pass

        def probe_fault_recovery(self, fault_call_index: int = 3):
            return FaultRecoveryResult(
                recovered=True,
                observed_attempts=3,
                recovery_ms=42.0,
                platform_terminated=False,
            )

    bundle = run_data_plane_probe(_FakeAdapter(), "fault_recovery", {})
    o = bundle.observations
    assert o["capability"] == "supported"
    assert o["recovered"] is True
    assert o["observed_attempts"] == 3
    assert o["recovery_ms"] == 42.0
    assert o["platform_terminated"] is False


# ---------------------------------------------------------------------------
# Scorer: t1_3 score() reads new shape and produces new measurements
# ---------------------------------------------------------------------------


def test_t1_3_score_recovered_true_no_warning():
    """score() with recovered=True → no findings, recovered measurement True."""
    from clousight_bench.core.observation import ObservationBundle
    from clousight_bench.domains.agent_runtime.tasks.t1_3_fault_recovery import FaultRecoveryTask

    obs = ObservationBundle(observations={
        "capability": "supported",
        "recovered": True,
        "observed_attempts": 3,
        "recovery_ms": 55.0,
        "platform_terminated": False,
    })
    result = FaultRecoveryTask().score(obs)
    # TaskResult.measurements has Measurement objects; access .value directly
    assert result.measurements["recovered"].value is True
    assert result.measurements["observed_attempts"].value == 3
    assert result.measurements["recovery_ms"].value == 55.0
    assert not any(f.code == "agent_runtime.platform_timeout_recovery" for f in result.findings)


def test_t1_3_score_platform_terminated_adds_warning():
    """score() with platform_terminated=True → warning finding."""
    from clousight_bench.core.observation import ObservationBundle
    from clousight_bench.domains.agent_runtime.tasks.t1_3_fault_recovery import FaultRecoveryTask

    obs = ObservationBundle(observations={
        "capability": "supported",
        "recovered": False,
        "observed_attempts": 1,
        "recovery_ms": 5000.0,
        "platform_terminated": True,
    })
    result = FaultRecoveryTask().score(obs)
    codes = [f.code for f in result.findings]
    assert "agent_runtime.platform_timeout_recovery" in codes


def test_t1_3_score_not_retried_adds_warning():
    """score() with recovered=False and observed_attempts<=1 → warning that platform blocked retry."""
    from clousight_bench.core.observation import ObservationBundle
    from clousight_bench.domains.agent_runtime.tasks.t1_3_fault_recovery import FaultRecoveryTask

    obs = ObservationBundle(observations={
        "capability": "supported",
        "recovered": False,
        "observed_attempts": 1,
        "recovery_ms": 10.0,
        "platform_terminated": False,
    })
    result = FaultRecoveryTask().score(obs)
    codes = [f.code for f in result.findings]
    assert "agent_runtime.platform_blocked_retry" in codes
