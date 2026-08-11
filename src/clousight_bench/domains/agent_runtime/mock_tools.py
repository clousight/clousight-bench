"""Fault-injectable mock tool server for the agent-runtime domain.

Pins the tool universe so the *runtime* is the only variable (StableToolBench
idea): otherwise you cannot tell a bad platform from a flaky upstream API.
Fault injection is deterministic and counter-based -> replayable by
construction: the Nth call to a target fails, no randomness.

Endpoints:
    GET  /health
    GET  /prices[?provider=&service=]      tool: read price API
    GET  /inventory                        tool: read resource inventory
    POST /reports                          tool: write report back (webhook)
    GET  /reports                          inspect written reports
    POST /reset                            clear reports + fault/latency config + counters
    POST /fault/config                     configure deterministic fault injection
    GET  /fault/state                      inspect call counters / active fault
    POST /latency/config                   configure deterministic latency injection
    GET  /latency/state                    inspect call counters / active latency

Fault config body (POST /fault/config):
    {"target": "prices", "fail_on_calls": [3], "status": 500}
    -> the 3rd call to /prices returns 500; subsequent calls recover.
    Or window form: {"target": "prices", "fail_from_call": 3, "fail_count": 2, "status": 503}

Run standalone (the runtime under test must be able to reach this address):
    python -m clousight_bench.domains.agent_runtime.mock_tools --port 8770
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from typing import Any
from urllib.parse import parse_qs, urlparse

DATA = Path(__file__).resolve().parent / "data"


def load_json(name: str) -> Any:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


class ToolState:
    """Server-side state: reports, per-target call counters, fault + latency config."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.reports: list[dict[str, Any]] = []
        self.call_counts: dict[str, int] = {}
        self.fault: dict[str, Any] | None = None
        self.latency: dict[str, Any] | None = None

    def reset(self) -> None:
        with self.lock:
            self.reports.clear()
            self.call_counts.clear()
            self.fault = None
            self.latency = None

    def next_call_index(self, target: str) -> int:
        with self.lock:
            self.call_counts[target] = self.call_counts.get(target, 0) + 1
            return self.call_counts[target]

    def fault_status_for(self, target: str, call_index: int) -> int | None:
        """Return an HTTP status to force, or None to serve normally. Deterministic."""
        with self.lock:
            fault = self.fault
        if not fault or fault.get("target") != target:
            return None
        status = int(fault.get("status", 500))
        if "fail_on_calls" in fault:
            return status if call_index in set(fault["fail_on_calls"]) else None
        if "fail_from_call" in fault:
            start = int(fault["fail_from_call"])
            count = int(fault.get("fail_count", 1))
            return status if start <= call_index < start + count else None
        return None

    def latency_for(self, target: str, call_index: int) -> int:
        """Return extra milliseconds to inject before serving, or 0. Deterministic.

        Config mirrors fault injection:
            {"target": "prices", "add_ms": 200}                 -> every call
            {"target": "prices", "add_ms": 200, "on_calls": [1]} -> only 1st call
            {"target": "prices", "add_ms": 200, "from_call": 3, "count": 2}
        """
        with self.lock:
            lat = self.latency
        if not lat or lat.get("target") != target:
            return 0
        add = int(lat.get("add_ms", 0))
        if "on_calls" in lat:
            return add if call_index in set(lat["on_calls"]) else 0
        if "from_call" in lat:
            start = int(lat["from_call"])
            count = int(lat.get("count", 1))
            return add if start <= call_index < start + count else 0
        return add


#: Header a client presents to authenticate against a token-locked mock server.
AUTH_HEADER = "X-Clousight-Token"


def make_handler(
    state: ToolState, token: str | None = None
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ClousightBenchMock/0.1"

        def _authorized(self) -> bool:
            """No token configured -> open (local-sim, 127.0.0.1). Token set ->
            every request must present it, EXCEPT /health, which reveals nothing
            and must stay reachable for preflight's reachability probe."""
            if not token:
                return True
            if urlparse(self.path).path == "/health":
                return True
            import hmac
            return hmac.compare_digest(self.headers.get(AUTH_HEADER) or "", token)

        def _send(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:  # keep benchmark output clean
            return

        def _maybe_fault(self, target: str) -> bool:
            """Gate a tool call: bump the call counter once, inject configured
            latency (sleep), then apply fault injection. Both selectors key off
            the SAME call_index so latency and fault stay aligned per call.
            Returns True if a fault was served (caller should stop)."""
            call_index = state.next_call_index(target)
            delay_ms = state.latency_for(target, call_index)
            if delay_ms:
                time.sleep(delay_ms / 1000)
            forced = state.fault_status_for(target, call_index)
            if forced is not None:
                self._send(
                    {"error": "injected_fault", "target": target, "call_index": call_index, "status": forced},
                    status=forced,
                )
                return True
            return False

        def do_GET(self) -> None:
            if not self._authorized():
                self._send({"error": "unauthorized"}, status=401)
                return
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send({"ok": True})
                return
            if parsed.path == "/fault/state":
                self._send({"call_counts": dict(state.call_counts), "fault": state.fault})
                return
            if parsed.path == "/latency/state":
                self._send({"call_counts": dict(state.call_counts), "latency": state.latency})
                return
            if parsed.path == "/prices":
                if self._maybe_fault("prices"):
                    return
                qs = parse_qs(parsed.query)
                provider = qs.get("provider", [None])[0]
                service = qs.get("service", [None])[0]
                products = load_json("prices.json")["products"]
                if provider:
                    products = [p for p in products if p["provider"] == provider]
                if service:
                    products = [p for p in products if p["service"] == service]
                self._send({"products": products})
                return
            if parsed.path == "/inventory":
                if self._maybe_fault("inventory"):
                    return
                self._send(load_json("resources.json"))
                return
            if parsed.path == "/reports":
                self._send({"reports": state.reports})
                return
            self._send({"error": "not_found", "path": parsed.path}, status=404)

        def do_POST(self) -> None:
            if not self._authorized():
                self._send({"error": "unauthorized"}, status=401)
                return
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                self._send({"error": "invalid_json"}, status=400)
                return
            if parsed.path == "/reports":
                if self._maybe_fault("reports"):
                    return
                with state.lock:
                    state.reports.append(payload)
                    report_id = len(state.reports)
                self._send({"ok": True, "report_id": report_id})
                return
            if parsed.path == "/fault/config":
                with state.lock:
                    state.fault = payload
                self._send({"ok": True, "fault": payload})
                return
            if parsed.path == "/latency/config":
                with state.lock:
                    state.latency = payload
                self._send({"ok": True, "latency": payload})
                return
            if parsed.path == "/reset":
                state.reset()
                self._send({"ok": True})
                return
            self._send({"error": "not_found", "path": parsed.path}, status=404)

    return Handler


def make_server(
    port: int, state: ToolState | None = None, token: str | None = None
) -> tuple[ThreadingHTTPServer, ToolState]:
    state = state or ToolState()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state, token))
    return server, state


def start_in_thread(
    port: int = 8770, token: str | None = None
) -> tuple[ThreadingHTTPServer, ToolState]:
    server, state = make_server(port, token=token)
    Thread(target=server.serve_forever, daemon=True).start()
    return server, state


def main() -> None:
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument(
        "--token", default=os.environ.get("CSBENCH_MOCK_TOKEN"),
        help="require this token in the X-Clousight-Token header (default: "
             "$CSBENCH_MOCK_TOKEN). Strongly recommended when exposing the server "
             "on a public tunnel for a real-cloud run.")
    args = parser.parse_args()
    server, _ = make_server(args.port, token=args.token)
    lock = " (token-locked)" if args.token else " (OPEN -- no token; do not expose publicly)"
    print(f"fault-injectable mock tool server on http://127.0.0.1:{args.port}{lock}")
    server.serve_forever()


if __name__ == "__main__":
    main()
