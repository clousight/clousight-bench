"""ProbeInvoker: the self-contained data-plane invoke toolkit.

Extracted from AliyunAgentRunTransport's data-plane seam so the load-generation
primitives run inside the in-region probe with no Aliyun SDK, no control
credentials, and no lazy provisioning — the runtime-under-test's public
data-plane endpoint (spec.target_endpoint) is all it needs.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from clousight_bench.domains.agent_runtime import protocol
from clousight_bench.domains.agent_runtime.adapters.base import (
    Attempt,
    InvocationTrace,
    ToolCall,
)

from .jobs import JobSpec


class ProbeInvoker:
    def __init__(self, spec: JobSpec) -> None:
        self._spec = spec
        self._http: Any = None
        self._session_ids: set[str] = set()
        self.last_ttft_ms: float | None = None
        self._last_trace_id: str | None = None
        self.collected_spans: list[dict] = []

    @property
    def arms_config(self) -> dict[str, Any] | None:
        cfg = self._spec.params.get("arms_config")
        return dict(cfg) if isinstance(cfg, dict) and cfg else None

    @property
    def session(self) -> Any:
        """Lazily create a requests.Session with connection pooling.

        requests.Session is thread-safe for concurrent GET/POST calls,
        and reuses HTTPS connections — avoids the SSL EOF errors that urllib
        produces under concurrent load (no pooling, one TCP connection per call).
        """
        if self._http is None:
            try:
                import requests
                from requests.adapters import HTTPAdapter
            except ImportError as exc:
                raise RuntimeError(
                    "the 'requests' library is required for data-plane HTTP calls. "
                    "Install it with: pip install requests"
                ) from exc
            s = requests.Session()
            # Allow up to 64 concurrent connections per host
            adapter = HTTPAdapter(pool_connections=4, pool_maxsize=64)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            self._http = s
        return self._http

    def create_session(self, spec: dict[str, Any] | None = None) -> str:
        # AgentRun has no CreateSession API: affinity is via X-AgentRun-Session-ID header.
        # Session creation is purely local: generate a UUID and track it for teardown.
        session_id = uuid.uuid4().hex
        self._session_ids.add(session_id)
        return session_id

    def destroy_session(self, session_id: str) -> None:
        self._session_ids.discard(session_id)

    def invoke(self, session_id: str, openai_body: dict[str, Any]) -> dict[str, Any]:
        endpoint_url = self._spec.target_endpoint
        if not endpoint_url:
            raise RuntimeError("ProbeInvoker.invoke: empty target_endpoint")

        # Call the endpoint's public URL directly (plain HTTPS POST, no Aliyun signing).
        # session_header_scheme provides session affinity for warm-path routing.
        # Use requests.Session for proper connection pooling under concurrent load.
        url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
        resp = self.session.post(
            url,
            json=openai_body,
            headers={self._spec.session_header_scheme: session_id},
            timeout=120,
        )
        resp.raise_for_status()
        # Capture trace ID from response headers for T4.x ARMS queries.
        for h in ("x-trace-id", "x-b3-traceid", "traceparent", "eagleeye-traceid"):
            tid = resp.headers.get(h) or resp.headers.get(h.upper())
            if tid:
                # traceparent format: 00-<trace_id>-<span_id>-<flags>
                if h == "traceparent" and "-" in tid:
                    tid = tid.split("-")[1]
                self._last_trace_id = tid
                break
        return resp.json()

    def measure_ttft(self, session_id: str, openai_body: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        """Send a streaming request and return (ttft_ms, full_response_dict).

        ttft_ms = wall-clock milliseconds from sending the request to receiving
        the first non-empty, non-comment SSE ``data:`` line. The full response is
        reassembled from all non-[DONE] data chunks.

        Falls back to a normal (non-streaming) invoke and returns (0.0, response)
        if the endpoint_url is not available or requests is not installed.
        """
        endpoint_url = self._spec.target_endpoint
        # Build a non-streaming fallback body (strip stream=True so invoke
        # receives a body it can parse as JSON rather than SSE).
        non_stream_body = {k: v for k, v in openai_body.items() if k != "stream"}

        if not endpoint_url:
            resp = self.invoke(session_id, non_stream_body)
            return 0.0, resp

        try:
            import requests as _requests  # noqa: F401 - import check only
        except ImportError:
            resp = self.invoke(session_id, non_stream_body)
            return 0.0, resp

        url = endpoint_url.rstrip("/") + "/openai/v1/chat/completions"
        session_obj = self.session
        t0 = time.perf_counter()
        try:
            http_resp = session_obj.post(
                url,
                json=openai_body,
                headers={self._spec.session_header_scheme: session_id},
                stream=True,
                timeout=120,
            )
            http_resp.raise_for_status()
        except Exception:
            # Fall back to normal invoke if streaming POST fails.
            resp = self.invoke(session_id, non_stream_body)
            return 0.0, resp

        # Check Content-Type: if the endpoint didn't return SSE, fall back to parsing
        # the response body as a normal JSON completion.
        content_type = str(http_resp.headers.get("Content-Type") or "")
        if "text/event-stream" not in content_type:
            # Deployed agent is an older version that doesn't support streaming.
            # Parse the body as normal JSON and return ttft = time to full response.
            try:
                full_resp = http_resp.json()
                ttft_ms = (time.perf_counter() - t0) * 1000
                return round(ttft_ms, 3), full_resp
            except Exception:
                resp = self.invoke(session_id, non_stream_body)
                return 0.0, resp

        ttft_ms = 0.0
        ttft_recorded = False
        chunks: list[str] = []
        try:
            for raw_line in http_resp.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                if not raw_line.startswith("data:"):
                    continue
                data_part = raw_line[5:].strip()
                if not ttft_recorded:
                    # Record TTFT at the first real data line (even if content is empty).
                    ttft_ms = (time.perf_counter() - t0) * 1000
                    ttft_recorded = True
                if data_part == "[DONE]":
                    break
                chunks.append(data_part)
        except Exception:
            # Stream read failed; fall back to a normal invoke for the result.
            if not ttft_recorded:
                ttft_ms = (time.perf_counter() - t0) * 1000
            resp = self.invoke(session_id, non_stream_body)
            return round(ttft_ms, 3), resp

        if not ttft_recorded:
            ttft_ms = (time.perf_counter() - t0) * 1000

        # Reassemble full response from all chunks by merging content deltas.
        import json as _json

        merged_content = ""
        for chunk_str in chunks:
            try:
                parsed = _json.loads(chunk_str)
                delta = (parsed.get("choices") or [{}])[0].get("delta") or {}
                merged_content += str(delta.get("content") or "")
            except (ValueError, TypeError):
                pass

        # Build a response that looks like a normal (non-streaming) chat completion.
        full_resp = {"choices": [{"message": {"role": "assistant", "content": merged_content}}]}
        return round(ttft_ms, 3), full_resp

    def run_tool_plan(self, session_id: str, plan: list[ToolCall]) -> InvocationTrace:
        # Data plane = the deployed runtime's OWN OpenAI-compatible endpoint, reached
        # with the session_header_scheme affinity header via the invoke seam.
        # One invoke per tool call, one attempt: the RUNTIME owns retries -- we
        # observe, never simulate.
        # For the first call (call_index == 1) we additionally measure TTFT via
        # streaming; the result is stored in self.last_ttft_ms for external queries.
        base = self._spec.mock_base_url
        mock_token = self._spec.mock_token
        # Inject arms_config when configured — agent.py will emit OpenInference spans
        # and embed them in the response body under "_spans" for get_trace() to collect.
        arms_cfg = self.arms_config
        attempts: list[Attempt] = []
        completed = True
        final_state = "completed"
        for call_index, call in enumerate(plan, start=1):
            tool = {"target": call.target, "method": call.method, "params": call.params, "body": call.body}
            if call_index == 1:
                # First invoke: measure TTFT with a streaming request.
                stream_body = protocol.encode_invoke_stream(tool, base, mock_token=mock_token or None)
                start = time.perf_counter()
                ttft_ms, resp = self.measure_ttft(session_id, stream_body)
                latency = (time.perf_counter() - start) * 1000
                self.last_ttft_ms = ttft_ms
            else:
                body = protocol.encode_invoke(tool, base, mock_token=mock_token or None, arms_config=arms_cfg)
                start = time.perf_counter()
                resp = self.invoke(session_id, body)
                latency = (time.perf_counter() - start) * 1000
            result = protocol.decode_result(resp)
            # Collect OpenInference spans embedded in the response by agent.py.
            if isinstance(result.get("_spans"), list):
                self.collected_spans.extend(result["_spans"])
            status = int(result.get("status", 0))
            ok = bool(result.get("ok"))
            attempts.append(Attempt(call_index, 1, status, ok, round(latency, 2)))
            if not ok:
                completed = False
                final_state = "failed"
                break
        return InvocationTrace(session_id, attempts, completed, final_state)

    def one_tool_call(self) -> tuple[bool, float]:
        ok, ms, _ = self.one_tool_call_classified()
        return ok, ms

    def one_tool_call_classified(self) -> tuple[bool, float, str]:
        """Like one_tool_call but also returns the error type.

        Returns (ok, latency_ms, error_type) where error_type is:
          ''          — success
          'transport' — SSL/connection error; runtime was never reached
          'runtime'   — runtime returned a non-2xx or tool call failed
        """
        import time as _time

        plan = [ToolCall(target="prices", params={"provider": "aliyun"})]
        session = self.create_session()
        t0 = _time.perf_counter()
        error_type = ""
        try:
            trace = self.run_tool_plan(session, plan)
            ok = trace.completed
            if not ok:
                error_type = "tool"  # AgentRun OK, mock tool failed
        except Exception as exc:
            ok = False
            err_str = str(exc).lower()
            if any(k in err_str for k in ("ssl", "connection", "eof", "timeout", "connect")):
                error_type = "transport"
            else:
                error_type = "runtime"  # AgentRun data-plane returned non-2xx
        finally:
            self.destroy_session(session)
        return ok, (_time.perf_counter() - t0) * 1000, error_type
