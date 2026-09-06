"""LangChain-based benchmark agent with OpenInference tracing.

Uses the real LangChain 1.x agent loop (``create_agent``, built on langgraph):
  BenchmarkChatModel — deterministic stub LLM (always calls the specified tool)
  MockServerTool     — LangChain BaseTool wrapping the mock HTTP server
  create_agent       — standard LangChain 1.x agent graph (instrumented by
                       OpenInference through langchain-core callbacks)

OpenInference instruments the agent graph, BaseChatModel and BaseTool, producing
genuine CHAIN / LLM / TOOL spans in a single trace with correct parent-child
linkage.  Spans are collected via InMemorySpanExporter and embedded in the
response body under ``_spans`` for in-band collection by the transport.

An ARMS OTLP export (best-effort, async) is added as a secondary exporter to
test the platform's real OTel pipeline end-to-end.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from typing import Any
from urllib import request as urlrequest

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import Field

# ---------------------------------------------------------------------------
# Pinned retry policy — part of the benchmark agent contract (not parameterised)
# ---------------------------------------------------------------------------

AGENT_RETRY_POLICY: dict[str, Any] = {
    "max_retries": 2,
    "backoff_ms": 200,
    "retry_on": "5xx",
}

# ---------------------------------------------------------------------------
# Deterministic stub LLM
# ---------------------------------------------------------------------------


class BenchmarkChatModel(BaseChatModel):
    """Always returns exactly the tool call specified at construction.

    Implements bind_tools() so create_agent() can register tools.
    OpenInference instruments _generate() → LLM span.
    """

    model_name: str = Field(default="clousight-bench-stub")
    tool_name: str = Field(default="prices")
    tool_args: dict = Field(default_factory=dict)
    _bound_tools: list = []

    @property
    def _llm_type(self) -> str:
        return "clousight-bench"

    def bind_tools(self, tools: list, **kwargs: Any) -> BenchmarkChatModel:
        """Required by create_agent; returns a copy (stub ignores tool schemas)."""
        copy = self.model_copy()
        copy._bound_tools = list(tools)
        return copy

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Implement the pinned retry policy.

        The agent loop: LLM emits tool_call → the tools node runs it → ToolMessage
        fed back → LLM called again.  We implement retry by re-emitting the same
        tool_call when the last ToolMessage carries a 5xx status and the retry
        budget (max_retries=2, i.e. ≤2 retries after the first attempt) is not
        exhausted.  Otherwise we emit a final answer to stop the loop.
        """
        from langchain_core.messages import ToolMessage

        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        attempt_count = len(tool_messages)  # number of tool calls already made

        if attempt_count == 0:
            # No tool result yet — first call, emit the tool_call.
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": self.tool_name,
                        "args": self.tool_args,
                        "id": "call_bench_001",
                        "type": "tool_call",
                    }
                ],
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

        # There is at least one ToolMessage — inspect the last one.
        last_tool_msg = tool_messages[-1]
        try:
            last_content = json.loads(last_tool_msg.content)
            http_status = int(last_content.get("_tool_http_status", 200))
        except Exception:
            http_status = 200  # unparseable → treat as success, stop loop

        should_retry = (
            500 <= http_status <= 599
            and http_status != 599  # 599 = connection failure, no retry
            and attempt_count <= AGENT_RETRY_POLICY["max_retries"]
        )

        if should_retry:
            time.sleep(AGENT_RETRY_POLICY["backoff_ms"] / 1000.0)
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": self.tool_name,
                        "args": self.tool_args,
                        "id": f"call_bench_{attempt_count + 1:03d}",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            message = AIMessage(content=f"Tool {self.tool_name!r} executed successfully.")

        return ChatResult(generations=[ChatGeneration(message=message)])


# ---------------------------------------------------------------------------
# Mock server tools
# ---------------------------------------------------------------------------


class MockServerTool(BaseTool):
    """LangChain tool backed by a mock HTTP server endpoint.

    OpenInference instruments run() → TOOL span.
    """

    name: str
    description: str
    mock_base_url: str = Field(default="")
    mock_token: str = Field(default="")
    http_method: str = Field(default="GET")

    def _run(self, **kwargs: Any) -> str:
        url = f"{self.mock_base_url.rstrip('/')}/{self.name}"
        if self.http_method == "GET" and kwargs:
            qs = "&".join(f"{k}={v}" for k, v in kwargs.items())
            url = f"{url}?{qs}"
        data = json.dumps(kwargs).encode() if self.http_method == "POST" else None
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.mock_token:
            headers["X-Clousight-Token"] = self.mock_token
        req = urlrequest.Request(url, data=data, method=self.http_method, headers=headers)
        import urllib.error

        try:
            with urlrequest.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                body["_tool_http_status"] = resp.status
                return json.dumps(body)
        except urllib.error.HTTPError as e:
            return json.dumps({"_tool_http_status": e.code, "error": str(e)})
        except Exception as exc:
            return json.dumps({"_tool_http_status": 599, "error": str(exc)})


def make_tools(mock_base_url: str, mock_token: str) -> list[BaseTool]:
    """Build the set of benchmark tools pointing to mock_base_url."""
    common = {"mock_base_url": mock_base_url, "mock_token": mock_token}
    return [
        MockServerTool(name="prices", description="Get current prices", **common),
        MockServerTool(name="inventory", description="Get inventory status", **common),
        MockServerTool(name="reports", description="Get analytics reports", **common),
    ]


# ---------------------------------------------------------------------------
# OTel / OpenInference setup (one-time, per-process)
# ---------------------------------------------------------------------------

_otel_lock = threading.Lock()
_otel_ready = False
_mem_exporter: Any = None


def setup_otel(arms_config: dict | None = None) -> bool:
    """Initialize OTel with InMemory + optional ARMS OTLP exporters.

    Idempotent. Returns True if OTel is ready.
    """
    global _otel_ready, _mem_exporter
    if _otel_ready:
        return True
    with _otel_lock:
        if _otel_ready:
            return True
        with contextlib.suppress(Exception):
            from openinference.instrumentation.langchain import LangChainInstrumentor
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor
            from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

            _mem_exporter = InMemorySpanExporter()
            provider = TracerProvider()
            provider.add_span_processor(SimpleSpanProcessor(_mem_exporter))

            if arms_config:
                lk = str(arms_config.get("license_key") or "")
                region = str(arms_config.get("region") or "cn-hangzhou")
                if lk:
                    _try_add_arms_exporter(provider, lk, region)

            trace.set_tracer_provider(provider)
            LangChainInstrumentor().instrument()
            _otel_ready = True
    return _otel_ready


def _try_add_arms_exporter(provider: Any, license_key: str, region: str) -> None:
    """Add async ARMS OTLP exporter. Best-effort — failures are silenced."""
    with contextlib.suppress(Exception):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        for ep in [
            f"http://arms-dc.{region}-internal.aliyuncs.com:8091/api/otlp/traces",
            f"http://arms-dc.{region}.aliyuncs.com:8091/api/otlp/traces",
        ]:
            try:
                exporter = OTLPSpanExporter(
                    endpoint=ep,
                    headers={"Authentication": license_key},
                    timeout=3,
                )
                provider.add_span_processor(BatchSpanProcessor(exporter))
                return
            except Exception:
                continue


# ---------------------------------------------------------------------------
# Main agent entry point
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = "You are a benchmark agent. Execute exactly the tool call requested."


def run(body: dict[str, Any]) -> dict[str, Any]:
    """Run the LangChain 1.x agent graph for one tool call; return result + OI spans.

    The create_agent graph is instrumented by OpenInference, producing:
      CHAIN span  — the agent graph invoke()
      LLM span    — BenchmarkChatModel._generate()
      TOOL span   — MockServerTool._run()
    All spans share a single trace_id with correct parent-child linkage.
    """
    tool_cfg = body.get("tool") or {}
    mock_base_url = str(body.get("mock_base_url") or "").rstrip("/")
    mock_token = str(body.get("mock_token") or "")
    arms_config = body.get("arms_config") or {}

    otel_ok = setup_otel(arms_config)
    if otel_ok and _mem_exporter is not None:
        _mem_exporter.clear()

    target = str(tool_cfg.get("target") or "prices")
    params = dict(tool_cfg.get("params") or {})

    llm = BenchmarkChatModel(tool_name=target, tool_args=params)
    tools = make_tools(mock_base_url, mock_token)

    agent = create_agent(llm, tools, system_prompt=_SYSTEM_PROMPT)
    # The stub LLM ends the loop itself once the retry budget is spent; the
    # recursion limit is only a safety net. Each attempt costs 2 graph steps
    # (model + tools), plus the final model step:
    # (max_retries + 1) * 2 + 1 = 7 — with headroom → 10.
    recursion_limit = (AGENT_RETRY_POLICY["max_retries"] + 1) * 2 + 4

    try:
        result = agent.invoke(
            {"messages": [HumanMessage(content=f"execute {target} tool call")]},
            config={"recursion_limit": recursion_limit},
        )
        ok = True
        status = 200
        messages = result.get("messages", [])
        final = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        output = str(final.content) if final is not None else ""
    except Exception as exc:
        ok = False
        status = 500
        output = str(exc)

    # Collect OpenInference spans from in-memory exporter
    spans: list[dict] = []
    if otel_ok and _mem_exporter is not None:
        for s in _mem_exporter.get_finished_spans():
            attrs = dict(s.attributes or {})
            spans.append(
                {
                    "trace_id": format(s.context.trace_id, "032x"),
                    "span_id": format(s.context.span_id, "016x"),
                    "parent_span_id": format(s.parent.span_id, "016x") if s.parent else "",
                    "name": s.name,
                    "kind": attrs.get("openinference.span.kind", "CHAIN"),
                    "attributes": attrs,
                }
            )

    result_body: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "tool_target": target,
        "tool_output": output,
    }
    if spans:
        result_body["_spans"] = spans
    return result_body
