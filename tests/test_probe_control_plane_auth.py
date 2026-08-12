"""C1 regression: control-plane fault/latency calls carry the mock auth token.

The mock server (mock_tools.py) rejects every request except /health with 401
when a token is configured.  Before the fix, the six direct control-plane calls
in probe/dataplane.py (POST /fault/config, GET /fault/state, POST /latency/config)
sent *no* auth header → the mock returned 401 → fault never injected →
observed_attempts stayed 0, recovered stayed False/True (constant verdict).

This test stands up:
  1. A real token-locked mock_tools server (make_server with token="testtoken").
  2. A minimal fake-agent server that reads the mock server's /prices endpoint
     (forwarding the correlation id and the mock token from the invoke payload)
     and simulates the lc_agent 5xx-retry-2 contract: 3 total attempts.
  3. Runs run_fault_recovery with spec.mock_token="testtoken".

With the fix: /fault/config and /fault/state carry the token → the mock
processes the fault config → the agent's calls hit the counter → observed_attempts
is the real call count (>= 1).

Without the fix: /fault/config returns 401 (silently swallowed) and /fault/state
returns 401 (silently swallowed) → observed_attempts == 0.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.domains.agent_runtime.mock_tools import AUTH_HEADER, make_server
from clousight_bench.domains.agent_runtime.probe.dataplane import run_fault_recovery
from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec

TOKEN = "testtoken"


def _start_mock(token: str | None = None):
    """Start a real mock_tools server. Returns (server, base_url)."""
    server, _state = make_server(0, token=token)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _start_agent(mock_base: str, token: str) -> tuple[ThreadingHTTPServer, str]:
    """Start a fake agent server.

    Parses the invoke payload to extract mock_base_url, mock_token, and
    _correlation_id, then makes 3 sequential GET /prices calls to the mock
    (forwarding the correlation id + auth token), simulating the lc_agent
    5xx-retry-2 contract.  Returns ok=True after 3 attempts (the first call
    will be faulted-500 per /fault/config, and subsequent calls succeed).
    """

    class _FakeAgent(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            n = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(n) or b"{}"
            body = json.loads(raw)
            payload = json.loads(body["messages"][0]["content"])
            corr = payload.get("_correlation_id") or ""
            m_base = payload.get("mock_base_url") or mock_base
            m_token = payload.get("mock_token") or token

            # Simulate 3 attempts — the mock will 500 on the first, then succeed.
            ok_final = False
            for _ in range(3):
                url = m_base.rstrip("/") + "/prices"
                req = urllib.request.Request(url)
                if corr:
                    req.add_header("X-Clousight-Correlation-Id", corr)
                if m_token:
                    req.add_header(AUTH_HEADER, m_token)
                try:
                    with urllib.request.urlopen(req, timeout=3) as r:
                        ok_final = r.status < 300
                        if ok_final:
                            break
                except Exception:
                    pass  # 500 from fault injection → continue retrying

            result = {"ok": ok_final, "status": 200 if ok_final else 500}
            content = json.dumps(result)
            out = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args: object) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAgent)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_fault_recovery_carries_token_to_mock_server():
    """run_fault_recovery with a token-locked mock must read the real call counter.

    Failure mode without fix: /fault/config → 401 (silently swallowed), fault
    never configured → mock server always serves 200 → agent makes exactly 1
    call (no retry needed) → observed_attempts == 1 (not useful) AND
    /fault/state → 401 (silently swallowed) → observed_attempts stays 0.

    With fix: /fault/config succeeds → fault is set (fail call #1) → agent
    hits /prices once (500), retries → makes 2+ total calls → /fault/state
    returns real counter → observed_attempts >= 2.
    """
    mock_srv, mock_base = _start_mock(token=TOKEN)
    agent_srv, agent_base = _start_agent(mock_base, TOKEN)

    spec = JobSpec(
        probe="fault_recovery",
        params={},
        target_endpoint=agent_base,
        mock_base_url=mock_base,
        mock_token=TOKEN,
    )

    try:
        bundle = run_fault_recovery(spec, lambda _p, _m: None)
    finally:
        mock_srv.shutdown()
        agent_srv.shutdown()

    o = bundle.observations
    # The fault was injected (fail_on_calls=[1]) so the agent made >= 2 attempts.
    # Without the fix, /fault/state returns 401 → swallowed → observed_attempts == 0.
    assert o["observed_attempts"] >= 2, (
        f"expected >= 2 observed_attempts (fault injected + at least one retry), "
        f"got {o['observed_attempts']} — likely /fault/config or /fault/state returned 401 "
        f"because the auth header was missing"
    )
