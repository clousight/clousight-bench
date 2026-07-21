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
    POST /reset                            clear reports + fault config + call counters
    POST /fault/config                     configure deterministic fault injection
    GET  /fault/state                      inspect call counters / active fault

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from typing import Any
from urllib.parse import parse_qs, urlparse

DATA = Path(__file__).resolve().parent / "data"


def load_json(name: str) -> Any:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


class ToolState:
    """Server-side state: written reports, per-target call counters, fault config."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.reports: list[dict[str, Any]] = []
        self.call_counts: dict[str, int] = {}
        self.fault: dict[str, Any] | None = None

    def reset(self) -> None:
        with self.lock:
            self.reports.clear()
            self.call_counts.clear()
            self.fault = None

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


def make_handler(state: ToolState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ClousightBenchMock/0.1"

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
            """Apply fault injection for a tool endpoint. Returns True if a fault was served."""
            call_index = state.next_call_index(target)
            forced = state.fault_status_for(target, call_index)
            if forced is not None:
                self._send(
                    {"error": "injected_fault", "target": target, "call_index": call_index, "status": forced},
                    status=forced,
                )
                return True
            return False

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send({"ok": True})
                return
            if parsed.path == "/fault/state":
                self._send({"call_counts": dict(state.call_counts), "fault": state.fault})
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
            if parsed.path == "/reset":
                state.reset()
                self._send({"ok": True})
                return
            self._send({"error": "not_found", "path": parsed.path}, status=404)

    return Handler


def make_server(port: int, state: ToolState | None = None) -> tuple[ThreadingHTTPServer, ToolState]:
    state = state or ToolState()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    return server, state


def start_in_thread(port: int = 8770) -> tuple[ThreadingHTTPServer, ToolState]:
    server, state = make_server(port)
    Thread(target=server.serve_forever, daemon=True).start()
    return server, state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    server, _ = make_server(args.port)
    print(f"fault-injectable mock tool server on http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
