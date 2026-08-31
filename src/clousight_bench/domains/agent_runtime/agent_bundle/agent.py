"""Clousight Bench benchmark agent -- the deployable artifact (zip code package).

The agent deployed onto a managed runtime (Aliyun AgentRun / AWS Bedrock AgentCore)
as the *payload under test*. It supports two execution modes:

1. **LangChain mode** (when ``arms_config`` is present in the request):
   Runs a real LangChain LCEL chain with a deterministic stub LLM and
   OpenInference instrumentation.  Produces genuine CHAIN / LLM / TOOL spans
   that measure the platform's agent observability pipeline end-to-end.

2. **Stub mode** (no arms_config, or LangChain not available):
   Makes the single requested tool call directly via HTTP.  Used for
   performance/reliability probes where tracing overhead is unwanted.

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

# Pinned retry policy — part of the benchmark agent contract. Applied on BOTH
# the stub/reliability path (handle_invoke) and the traced path (lc_agent), so
# reliability probes observe the agent's real retry behavior
# via the mock's per-correlation call counter. Kept in lock-step with
# lc_agent.AGENT_RETRY_POLICY (a drift-guard test asserts they are equal).
AGENT_RETRY_POLICY: dict[str, Any] = {"max_retries": 2, "backoff_ms": 200, "retry_on": "5xx"}

try:
    from clousight_bench.domains.agent_runtime import protocol
except ImportError:  # pragma: no cover - the flattened-zip path
    import protocol  # type: ignore[no-redef]


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
    """Make the requested tool call against the mock universe, applying the pinned
    agent retry policy, and report the final raw HTTP outcome.

    Per AGENT_RETRY_POLICY: a transient 5xx (500-598) is retried up to
    ``max_retries`` times with a fixed backoff; 4xx and connection failures (599)
    are terminal and never retried. Each attempt re-issues the SAME request (same
    correlation id), so reliability probes can read the agent's
    attempt count from the mock's per-correlation call counter -- the agent owns
    the retry; the platform is measured for whether it lets the retries through."""
    tool = body.get("tool") or {}
    base = str(body.get("mock_base_url") or "").rstrip("/")
    mock_token = str(body.get("mock_token") or "")
    target = str(tool.get("target") or "")
    method = str(tool.get("method") or "GET").upper()
    params = tool.get("params") or {}
    payload = tool.get("body") or {}

    corr = str(body.get("_correlation_id") or "")

    url = f"{base}/{target}"
    if method == "GET" and params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    data = json.dumps(payload).encode("utf-8") if method == "POST" else None
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if mock_token:
        headers["X-Clousight-Token"] = mock_token
    if corr:
        headers["X-Clousight-Correlation-Id"] = corr
    req = urlrequest.Request(url, data=data, method=method, headers=headers)

    max_attempts = AGENT_RETRY_POLICY["max_retries"] + 1  # 1 initial + N retries
    status = 599
    for attempt in range(1, max_attempts + 1):
        try:
            with urlrequest.urlopen(req, timeout=10) as resp:
                status = resp.status
                resp.read()
        except Exception as exc:  # noqa: BLE001
            status = int(getattr(exc, "code", 599))
        # Retry only transient 5xx (500-598). 599 = connection failure and 4xx
        # are terminal. Re-issue the SAME request so the mock counts each attempt
        # under this correlation bucket.
        if 500 <= status <= 598 and attempt < max_attempts:
            time.sleep(AGENT_RETRY_POLICY["backoff_ms"] / 1000.0)
            continue
        break
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

    This dual approach ensures tracing always gets proper OpenInference spans even if
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


# ---------------------------------------------------------------------------
# SWE mode (SWE-bench oracle / llm)
# ---------------------------------------------------------------------------

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

SWE_SYSTEM_PROMPT = (
    "You are an expert software engineer. Given a bug report for a repository, "
    "produce a unified diff patch that fixes the problem. "
    "Output ONLY the unified diff (starting with 'diff --git' or '--- '), "
    "with no explanation, no commentary, and no markdown code fences."
)

_ZERO_USAGE: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _extract_diff(reply: str) -> str:
    """Pull the unified diff out of an LLM reply.

    Diff markers win over fences: take from the first ``diff --git`` / ``--- ``
    LINE onward (dropping a trailing line-start closing fence), so an earlier
    prose fence can never shadow the real diff and a fence CHARACTER inside the
    diff body never truncates it.  A reply with no diff marker anywhere yields
    ``""`` — "no patch produced" — never prose masquerading as a patch.
    """
    lines = reply.strip().split("\n")
    start = next(
        (i for i, ln in enumerate(lines) if ln.startswith("diff --git") or ln.startswith("--- ")),
        None,
    )
    if start is None:
        return ""
    body = lines[start:]
    # Drop a trailing closing fence (must be at line start — ``` inside a diff
    # that patches markdown is content, not a fence).
    end = next((i for i, ln in enumerate(body) if ln.startswith("```")), len(body))
    return "\n".join(body[:end]).strip()


def _normalize_usage(raw: dict[str, Any]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for key in _ZERO_USAGE:
        try:
            usage[key] = int(raw.get(key) or 0)
        except (TypeError, ValueError):
            usage[key] = 0
    return usage


def _swe_span_dict(span: _Span, status: str, error: str = "") -> dict[str, Any]:
    """Embeddable span dict, same shape as the tool-plan path plus status/error."""
    if error:
        span.set_attr("error", error)
    out: dict[str, Any] = {
        "trace_id": span.trace_id,
        "span_id": span.span_id,
        "parent_span_id": span.parent_span_id,
        "name": span.name,
        "kind": span.kind,
        "status": status,
        "attributes": {**span.attrs},
    }
    if error:
        out["error"] = error
    return out


def _call_dashscope(model: str, problem: str, hints: str, api_key: str) -> tuple[str, dict, str]:
    """POST to the DashScope OpenAI-compatible endpoint.

    Returns ``(reply_text, raw_usage, error)``; on any HTTP failure error is a
    non-empty summary and the other fields are empty — never raises.
    """
    user_content = problem if not hints else f"{problem}\n\nHints:\n{hints}"
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SWE_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    req = urlrequest.Request(DASHSCOPE_URL, data=body, method="POST", headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - the runtime must never crash
        return "", {}, f"dashscope request failed: {exc}"[:500]
    choices = data.get("choices") or []
    message = choices[0].get("message") or {} if choices and isinstance(choices[0], dict) else {}
    return str(message.get("content") or ""), dict(data.get("usage") or {}), ""


def handle_swe(swe: dict[str, Any]) -> dict[str, Any]:
    """Run a SWE-bench instance in oracle or llm mode.

    oracle → echo the gold patch verbatim (one CHAIN span, zero usage).
    llm    → ask DashScope for a unified diff (one LLM span, usage from the API).
    Failures (missing DASHSCOPE_API_KEY, HTTP errors) yield an empty patch and a
    status="error" span — the runtime never raises.
    """
    trace_id = uuid.uuid4().hex + uuid.uuid4().hex[:16]
    instance_id = str(swe.get("instance_id") or "")
    if str(swe.get("agent_mode") or "") == "oracle":
        span = _Span(trace_id, "swe-oracle", "CHAIN")
        span.set_attr("swe.instance_id", instance_id)
        span.finish()
        return {
            "model_patch": str(swe.get("gold_patch") or ""),
            "usage": dict(_ZERO_USAGE),
            "_spans": [_swe_span_dict(span, "ok")],
        }
    model = str(swe.get("llm_model") or "qwen-plus")
    span = _Span(trace_id, "swe-llm", "LLM")
    span.set_attr("llm.model_name", model)
    span.set_attr("swe.instance_id", instance_id)
    api_key = os.environ.get("DASHSCOPE_API_KEY") or ""
    if not api_key:
        span.finish()
        return {
            "model_patch": "",
            "usage": dict(_ZERO_USAGE),
            "_spans": [_swe_span_dict(span, "error", "DASHSCOPE_API_KEY not set")],
        }
    reply, raw_usage, error = _call_dashscope(
        model, str(swe.get("problem_statement") or ""), str(swe.get("hints") or ""), api_key
    )
    span.finish()
    if error:
        return {
            "model_patch": "",
            "usage": dict(_ZERO_USAGE),
            "_spans": [_swe_span_dict(span, "error", error)],
        }
    return {
        "model_patch": _extract_diff(reply),
        "usage": _normalize_usage(raw_usage),
        "_spans": [_swe_span_dict(span, "ok")],
    }


def handle_chat_completion(openai_body: dict[str, Any]) -> dict[str, Any]:
    """OpenAI /chat/completions wrapper: decode the tool plan, run the agent.

    A payload carrying a ``swe`` key routes to the SWE-bench mode (oracle/llm).
    Otherwise, when ``arms_config`` is present, runs the full LangChain LCEL
    chain with OpenInference instrumentation (tracing mode).  Falls back to
    the stub path when LangChain is unavailable or arms_config is absent.
    """
    req = protocol.decode_request(openai_body)
    if req.get("swe"):
        swe = req["swe"]
        # A crafted/corrupt non-dict payload degrades like a config error,
        # never crashes an in-process caller.
        return protocol.encode_result(handle_swe(swe if isinstance(swe, dict) else {}))
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

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        # Readiness probe. The server only binds + accepts connections once the
        # (slow) cold start has finished loading the agent, so a 200 here means
        # "warm". The transport polls this before measuring latency so cold-start
        # time is not folded into e.g. TTFT (which should be warm-path only).
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "5")
        self.end_headers()
        self.wfile.write(b"ready")

    def log_message(self, *args: Any) -> None:  # silence per-request logging
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Clousight Bench benchmark agent")
    ap.add_argument("--port", type=int, default=9000)
    args = ap.parse_args()
    ThreadingHTTPServer(("0.0.0.0", args.port), _Handler).serve_forever()


if __name__ == "__main__":
    main()
