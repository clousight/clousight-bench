"""Local simulated runtime adapter.

Proves the harness end-to-end WITHOUT any cloud account: it models a platform
runtime with a configurable recovery policy, so tasks can verify they correctly
distinguish fail-fast vs auto-retry behavior before real adapters exist.

Real adapters (aliyun / huawei / volcengine) implement the same base against
live platforms; they must NOT reimplement task or scoring logic.
"""
from __future__ import annotations

import json
import time
from typing import Any
from urllib import request

from clousight_bench.domains.agent_runtime import openinference
from clousight_bench.domains.agent_runtime.adapters.base import (
    AgentRuntimeAdapter,
    Attempt,
    CapabilityNotSupported,
    InvocationTrace,
    ToolCall,
)


class LocalSimAdapter(AgentRuntimeAdapter):
    name = "local-sim"
    status = "reference"
    provider = None

    def __init__(self, target: dict[str, Any] | None = None) -> None:
        super().__init__(target)
        # recovery policy this simulated runtime applies on tool failure
        recovery = self.target.get("recovery", {})
        self.recovery_mode: str = recovery.get("mode", "auto-retry")  # "auto-retry" | "fail-fast"
        self.max_retries: int = int(recovery.get("max_retries", 3))
        self.backoff_ms: list[int] = list(recovery.get("backoff_ms", [50, 100, 200]))
        # Configurable capability policies (so tasks can observe both support
        # and absence deterministically without any cloud account).
        self.state_persistence: str = self.target.get("state_persistence", "durable")
        self.supported_registration_paths: list[str] = list(
            self.target.get("tool_registration", ["mcp", "openapi", "native"])
        )
        trace_cfg = self.target.get("trace", {})
        self.trace_completeness: str = trace_cfg.get("completeness", "full")  # "full" | "partial"
        self.otel_export_enabled: bool = bool(trace_cfg.get("otel_export", True))
        self._session_seq = 0
        self._mock_server = None
        self._state: dict[str, dict[str, Any]] = {}
        self._last_calls: dict[str, int] = {}

    def setup(self) -> None:
        """Start the pinned tool universe in-process.

        Default port 0 -> the OS assigns a free ephemeral port, so a local run
        never collides with a system service (macOS sharingd, etc.) or a stale
        server. The port is an environment detail, not a tested variable, so it
        is intentionally kept out of config_hash.
        """
        from clousight_bench.domains.agent_runtime.mock_tools import start_in_thread

        port = int(self.target.get("mock_port", 0))
        self._mock_server, _ = start_in_thread(port)
        actual_port = self._mock_server.server_address[1]
        self.mock_base_url = f"http://127.0.0.1:{actual_port}"

    def teardown(self) -> None:
        if self._mock_server is not None:
            self._mock_server.shutdown()
            self._mock_server = None

    def create_session(self, spec: dict[str, Any] | None = None) -> str:
        self._session_seq += 1
        return f"local-sim-{self._session_seq}"

    def destroy_session(self, session_id: str) -> None:
        return None

    def _http(self, call: ToolCall) -> tuple[int, float]:
        url = f"{self.mock_base_url.rstrip('/')}/{call.target}"
        if call.method == "GET" and call.params:
            qs = "&".join(f"{k}={v}" for k, v in call.params.items())
            url = f"{url}?{qs}"
        data = json.dumps(call.body).encode("utf-8") if call.method == "POST" else None
        req = request.Request(url, data=data, method=call.method,
                              headers={"Content-Type": "application/json"})
        start = time.perf_counter()
        try:
            with request.urlopen(req, timeout=10) as resp:
                status = resp.status
                resp.read()
        except Exception as exc:  # HTTPError carries .code; other errors -> 599
            status = getattr(exc, "code", 599)
        return status, (time.perf_counter() - start) * 1000

    def run_tool_plan(self, session_id: str, plan: list[ToolCall]) -> InvocationTrace:
        attempts: list[Attempt] = []
        completed = True
        final_state = "completed"
        for call_index, call in enumerate(plan, start=1):
            attempt_no = 0
            while True:
                attempt_no += 1
                status, latency = self._http(call)
                ok = 200 <= status < 300
                attempts.append(Attempt(call_index, attempt_no, status, ok, round(latency, 2)))
                if ok:
                    break
                # tool failed -> apply this runtime's recovery policy
                if self.recovery_mode == "fail-fast" or attempt_no > self.max_retries:
                    completed = False
                    final_state = "aborted" if self.recovery_mode == "fail-fast" else "failed"
                    break
                backoff = self.backoff_ms[min(attempt_no - 1, len(self.backoff_ms) - 1)]
                time.sleep(backoff / 1000)
            if not completed:
                break
        self._last_calls[session_id] = len(plan)
        return InvocationTrace(session_id, attempts, completed, final_state)

    # --- Capability implementations (configurable, deterministic) ------------

    def persist_state(self, session_id: str, state: dict[str, Any]) -> None:
        self._state[session_id] = dict(state)

    def load_state(self, session_id: str) -> dict[str, Any]:
        return dict(self._state.get(session_id, {}))

    def resume_session(self, session_id: str) -> str:
        # Durable runtimes keep session state across an interruption; ephemeral
        # ones lose it. The session id is stable across resume.
        if self.state_persistence == "ephemeral":
            self._state.pop(session_id, None)
        return session_id

    def register_tool(self, path: str, spec: dict[str, Any]) -> bool:
        if path not in ("mcp", "openapi", "native"):
            raise ValueError(f"unknown registration path: {path!r}")
        return path in self.supported_registration_paths

    def get_trace(self, session_id: str) -> list[dict[str, Any]]:
        tool_calls = self._last_calls.get(session_id, 0)
        drop = ("TOOL",) if self.trace_completeness == "partial" else ()
        return openinference.build_spans(session_id, tool_calls, drop_kinds=drop)

    def export_otel(self, session_id: str) -> dict[str, Any]:
        if not self.otel_export_enabled:
            raise CapabilityNotSupported("export_otel")
        spans = self.get_trace(session_id)
        return openinference.to_otel(spans, service_name=self.name)
