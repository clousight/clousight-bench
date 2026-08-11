"""Clousight Bench benchmark agent -- the deployable artifact (zip code package).

The agent deployed onto a managed runtime (Aliyun AgentRun / AWS Bedrock AgentCore)
as the *payload under test*. It supports two execution modes:

1. **LangChain mode** (when ``arms_config`` is present in the request):
   Runs a real LangChain LCEL chain with a deterministic stub LLM and
   OpenInference instrumentation.  Produces genuine CHAIN / LLM / TOOL spans
   that measure the platform's agent observability pipeline end-to-end.

2. **Stub mode** (no arms_config, or LangChain not available):
   Makes the single requested tool call directly via HTTP.  Used for
   performance/reliability tasks (T1.x) where tracing overhead is unwanted.

Invoke contract:
    request  = {"tool": {...}, "mock_base_url": "...", "arms_config": {...}}
    response = {"ok": bool, "status": int, "tool_target": str,
                "_spans": [...]?}   # present in LangChain mode

The zip ships a ``vendor/`` directory with langchain-core + openinference +
opentelemetry.  agent.py adds it to sys.path before importing these packages.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import request as urlrequest

# Add vendor directory to sys.path so langchain-core / openinference are importable
# inside the FC function (the zip flattens everything to one root).
_here = os.path.dirname(os.path.abspath(__file__))
for _candidate in (
    os.path.join(_here, "vendor"),  # flat-zip layout
    os.path.join(_here, "..", "vendor"),  # package layout
):
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)
        break

try:
    from clousight_bench.domains.agent_runtime import protocol
except ImportError:  # pragma: no cover - the flattened-zip path
    import protocol  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Request-level fault injection state
# ---------------------------------------------------------------------------
# Per-session call counter for request-level fault injection (T1.3).
# Used by handle_invoke() when fail_after_n_calls is set in the request body.
# State is process-local (single FC instance) — that is exactly the point:
# by encoding the fault spec in the request, T1.3 avoids the multi-instance
# state-sharing problem that plagued the old POST /fault/config approach.
_call_counter: dict[str, int] = {}


# ---------------------------------------------------------------------------
# OpenInference tracing (stdlib-only OTLP/HTTP export to ARMS)
# ---------------------------------------------------------------------------


class _Span:
    """Minimal span context for OpenInference semantic conventions."""

    def __init__(self, trace_id: str, name: str, kind: str, parent_span_id: str = "") -> None:
        self.trace_id = trace_id
        self.span_id = uuid.uuid4().hex[:16]
        self.parent_span_id = parent_span_id
        self.name = name
        self.kind = kind  # "CHAIN" | "LLM" | "TOOL"
        self.start_ns = time.perf_counter_ns()
        self.end_ns: int = 0
        self.attrs: dict[str, str] = {"openinference.span.kind": kind}

    def finish(self) -> None:
        self.end_ns = time.perf_counter_ns()

    def set_attr(self, key: str, value: str) -> None:
        self.attrs[key] = value

    def to_otlp(self) -> dict:
        epoch_ns = int(time.time() * 1e9)
        _elapsed = self.start_ns  # perf_counter_ns — need wall-clock base
        start_wall = epoch_ns - (time.perf_counter_ns() - self.start_ns)
        end_wall = epoch_ns - (time.perf_counter_ns() - (self.end_ns or time.perf_counter_ns()))
        return {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "parentSpanId": self.parent_span_id,
            "name": self.name,
            "kind": 1,  # SPAN_KIND_SERVER
            "startTimeUnixNano": str(start_wall),
            "endTimeUnixNano": str(end_wall),
            "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in self.attrs.items()],
            "status": {"code": 1},
        }


def _export_spans(spans: list[_Span], arms_config: dict) -> None:
    """Best-effort OTLP/HTTP export to ARMS via multiple endpoint candidates.

    Tries the Aliyun FC internal ARMS endpoint (accessible from inside VPC)
    and the public endpoint as fallback. Export failures are silently swallowed
    so they never affect the tool-call result.
    """
    license_key = str(arms_config.get("license_key") or "")
    region = str(arms_config.get("region") or "cn-hangzhou")
    if not license_key or not spans:
        return

    payload = json.dumps(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "clousight-bench-agent"}},
                            {"key": "arms.licenseKey", "value": {"stringValue": license_key}},
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "openinference", "version": "0.1"},
                            "spans": [s.to_otlp() for s in spans],
                        }
                    ],
                }
            ]
        }
    ).encode("utf-8")

    headers = {"Content-Type": "application/json", "Authentication": license_key}
    endpoints = [
        # FC-internal ARMS endpoint (preferred from inside Aliyun VPC/FC)
        f"http://arms-dc.{region}-internal.aliyuncs.com:8091/api/otlp/traces",
        f"http://arms-dc.{region}.aliyuncs.com:8091/api/otlp/traces",
        # Try public endpoint without internal suffix
        f"http://arms-dc.{region}.aliyuncs.com:8090/api/otlp/traces",
    ]
    for ep in endpoints:
        try:
            req = urlrequest.Request(ep, data=payload, method="POST", headers=headers)
            with urlrequest.urlopen(req, timeout=5) as resp:
                if resp.status < 300:
                    return
        except Exception:
            continue


def handle_invoke(body: dict[str, Any]) -> dict[str, Any]:
    """Make the single requested tool call against the mock universe and report
    its raw HTTP outcome. No retries, no interpretation -- the runtime owns those."""
    tool = body.get("tool") or {}
    base = str(body.get("mock_base_url") or "").rstrip("/")
    mock_token = str(body.get("mock_token") or "")
    target = str(tool.get("target") or "")
    method = str(tool.get("method") or "GET").upper()
    params = tool.get("params") or {}
    payload = tool.get("body") or {}

    # Request-level fault injection: synthetic failure on nth call.
    # Used by T1.3 to test recovery behavior without relying on FC mock-server state.
    # When fail_after_n_calls=N is set in the request body, the Nth call from the same
    # session returns ok=False/_fault_injected=True without touching the mock server.
    fail_after = int(body.get("fail_after_n_calls") or 0)
    if fail_after > 0:
        session_id = str(body.get("_session_id") or "default")
        _call_counter[session_id] = _call_counter.get(session_id, 0) + 1
        if _call_counter[session_id] >= fail_after:
            del _call_counter[session_id]
            return {"ok": False, "status": 500, "tool_target": target, "_fault_injected": True}

    url = f"{base}/{target}"
    if method == "GET" and params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    data = json.dumps(payload).encode("utf-8") if method == "POST" else None
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if mock_token:
        headers["X-Clousight-Token"] = mock_token
    req = urlrequest.Request(url, data=data, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=10) as resp:
            status = resp.status
            resp.read()
    except Exception as exc:  # noqa: BLE001
        status = int(getattr(exc, "code", 599))
    return {"ok": 200 <= status < 300, "status": status, "tool_target": target}


def handle_invoke_traced(body: dict[str, Any]) -> dict[str, Any]:
    """Like handle_invoke but wraps the call in OpenInference CHAIN/LLM/TOOL spans.

    Emits three spans:
      CHAIN — the overall agent invocation
      LLM   — "LLM decision": decode the request and decide which tool to call
               (deterministic stub — the tool is already specified in the request)
      TOOL  — the actual HTTP call to the mock tool server

    The spans are:
    1. Embedded in the response under ``_spans`` for the transport to read (primary).
    2. Best-effort exported to ARMS via OTLP/HTTP (secondary; may fail in some envs).

    This dual approach ensures T4.x always gets proper OpenInference spans even if
    the ARMS OTLP endpoint is unreachable from inside the FC function.
    """
    arms_config: dict = body.get("arms_config") or {}
    fc_ctx = body.get("_fc_trace_ctx") or {}
    trace_id = str(fc_ctx.get("trace_id") or "") or (uuid.uuid4().hex + uuid.uuid4().hex[:16])
    fc_parent_span = str(fc_ctx.get("parent_span_id") or "")

    chain = _Span(trace_id, "agent.invoke", "CHAIN", parent_span_id=fc_parent_span)
    chain.set_attr("agent.type", "clousight-bench")

    llm = _Span(trace_id, "llm.plan", "LLM", parent_span_id=chain.span_id)
    llm.set_attr("llm.model_name", "clousight-bench-stub")
    tool = body.get("tool") or {}
    target = str(tool.get("target") or "")
    llm.set_attr("llm.output.tool_name", target)
    llm.finish()

    tool_span = _Span(trace_id, f"tool.call.{target}", "TOOL", parent_span_id=chain.span_id)
    tool_span.set_attr("tool.name", target)
    tool_span.set_attr("tool.parameters", json.dumps(tool.get("params") or {}))

    result = handle_invoke(body)

    tool_span.set_attr("tool.output", json.dumps({"ok": result.get("ok"), "status": result.get("status")}))
    tool_span.finish()
    chain.finish()

    spans = [chain, llm, tool_span]

    # Best-effort ARMS export (may fail if endpoint unreachable from FC env)
    if arms_config:
        _export_spans(spans, arms_config)

    # Embed spans in response — transport reads _spans to build get_trace() result.
    result["_spans"] = [
        {
            "trace_id": s.trace_id,
            "span_id": s.span_id,
            "parent_span_id": s.parent_span_id,
            "name": s.name,
            "kind": s.kind,
            "attributes": {**s.attrs},
        }
        for s in spans
    ]
    return result


def handle_chat_completion(openai_body: dict[str, Any]) -> dict[str, Any]:
    """OpenAI /chat/completions wrapper: decode the tool plan, run the agent.

    When ``arms_config`` is present, runs the full LangChain LCEL chain with
    OpenInference instrumentation (T4.x tracing mode).  Falls back to the stub
    path when LangChain is unavailable or arms_config is absent.
    """
    req = protocol.decode_request(openai_body)
    if req.get("arms_config"):
        # Try LangChain mode first (genuine CHAIN/LLM/TOOL spans)
        try:
            try:
                from clousight_bench.domains.agent_runtime.agent_bundle import lc_agent
            except ImportError:
                import lc_agent  # type: ignore[no-redef]  # flat-zip path
            result = lc_agent.run(req)
        except Exception:
            # LangChain unavailable (vendor not bundled) → fall back to stub
            result = handle_invoke_traced(req)
    else:
        result = handle_invoke(req)
    return protocol.encode_result(result)


def _extract_fc_trace_ctx(headers: Any) -> dict[str, str]:
    """Extract FC/ARMS trace context from HTTP request headers.

    Aliyun FC injects eagleeye-traceid + eagleeye-rpcid (or X-B3-TraceId) into
    every function invocation. Capturing these lets us attach our OpenInference
    child spans to the FC-level trace in ARMS.
    """
    ctx: dict[str, str] = {}
    for h_name, ctx_key in [
        ("eagleeye-traceid", "trace_id"),
        ("x-b3-traceid", "trace_id"),
        ("eagleeye-rpcid", "rpc_id"),
        ("x-b3-spanid", "parent_span_id"),
    ]:
        val = headers.get(h_name) or headers.get(h_name.upper())
        if val and ctx_key not in ctx:
            ctx[ctx_key] = str(val)
    return ctx


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        # Capture FC trace context for ARMS child-span attachment.
        fc_trace_ctx = _extract_fc_trace_ctx(self.headers)
        try:
            body = json.loads(raw or b"{}")
            # Inject FC trace context so handle_invoke_traced can use it.
            if fc_trace_ctx:
                body.setdefault("_fc_trace_ctx", fc_trace_ctx)
            is_chat = self.path.endswith("/chat/completions")
            is_stream = is_chat and bool(body.get("stream"))
            if is_stream:
                # SSE streaming path: TTFT = time to first non-empty data: line.
                # Send 200 headers + a first empty-delta chunk BEFORE executing the
                # tool call so the transport records TTFT = network latency, not full
                # tool-call RTT. Flush immediately to push the bytes to the client.
                # No Content-Length: SSE streams are connection-close delimited.
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                first_chunk = json.dumps({"choices": [{"delta": {"content": ""}, "finish_reason": None}]})
                self.wfile.write(f"data: {first_chunk}\n\n".encode())
                self.wfile.flush()  # push first chunk to client immediately
                # Execute tool call (may take hundreds of ms after the flush).
                result = handle_chat_completion(body)
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                full_chunk = json.dumps(
                    {"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]}
                )
                self.wfile.write(f"data: {full_chunk}\n\ndata: [DONE]\n\n".encode())
                self.wfile.flush()
                return
            if is_chat:
                result = handle_chat_completion(body)
            else:
                result = handle_invoke(body)
            code = 200
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": str(exc)}
            code = 500
        out = json.dumps(result).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args: Any) -> None:  # silence per-request logging
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Clousight Bench benchmark agent")
    ap.add_argument("--port", type=int, default=9000)
    args = ap.parse_args()
    ThreadingHTTPServer(("0.0.0.0", args.port), _Handler).serve_forever()


if __name__ == "__main__":
    main()
