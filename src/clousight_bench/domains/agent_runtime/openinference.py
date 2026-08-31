"""OpenInference / OTel helpers for agent-runtime trace scoring.

We adopt a small, stable subset of the OpenInference semantic conventions so
Span completeness and OTel export compatibility measure the same
thing across platforms:

- Each span carries ``openinference.span.kind`` in {CHAIN, LLM, TOOL}.
- A well-formed agent invocation trace has exactly one root CHAIN span, at
  least one LLM span, and one TOOL span per tool call.

``build_spans`` turns an InvocationTrace into this shape (used by local-sim and
as the reference the scorer compares real platform traces against).
``to_otel`` maps those spans to a minimal OTLP resourceSpans structure so the
OTel-export probe can validate a runtime's OTel export against a known-good schema.
"""

from __future__ import annotations

from typing import Any

SPAN_KINDS = ("CHAIN", "LLM", "TOOL")
_KIND_ATTR = "openinference.span.kind"


def build_spans(
    session_id: str, tool_calls: int, *, drop_kinds: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """Build a reference OpenInference span list for an invocation.

    One root CHAIN, one LLM (planning), and one TOOL span per tool call.
    ``drop_kinds`` lets a simulated runtime omit span kinds to model an
    incomplete tracer.
    """
    spans: list[dict[str, Any]] = []
    if "CHAIN" not in drop_kinds:
        spans.append(
            {
                "span_id": f"{session_id}-chain",
                "parent_id": None,
                "name": "agent.invocation",
                "attributes": {_KIND_ATTR: "CHAIN"},
            }
        )
    if "LLM" not in drop_kinds:
        spans.append(
            {
                "span_id": f"{session_id}-llm",
                "parent_id": f"{session_id}-chain",
                "name": "llm.plan",
                "attributes": {_KIND_ATTR: "LLM"},
            }
        )
    if "TOOL" not in drop_kinds:
        for i in range(1, tool_calls + 1):
            spans.append(
                {
                    "span_id": f"{session_id}-tool-{i}",
                    "parent_id": f"{session_id}-chain",
                    "name": f"tool.call.{i}",
                    "attributes": {_KIND_ATTR: "TOOL"},
                }
            )
    return spans


def expected_span_count(tool_calls: int) -> int:
    """1 CHAIN + 1 LLM + one TOOL per tool call."""
    return 2 + tool_calls


def span_completeness(spans: list[dict[str, Any]], tool_calls: int) -> float:
    """Fraction of the expected spans that are present (0.0..1.0)."""
    expected = expected_span_count(tool_calls)
    if expected == 0:
        return 1.0
    return round(min(len(spans), expected) / expected, 4)


def kinds_present(spans: list[dict[str, Any]]) -> set[str]:
    return {s.get("attributes", {}).get(_KIND_ATTR) for s in spans} - {None}


def to_otel(spans: list[dict[str, Any]], service_name: str) -> dict[str, Any]:
    """Map OpenInference spans to a minimal OTLP resourceSpans structure."""
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service_name}}]},
                "scopeSpans": [
                    {
                        "scope": {"name": "clousight-bench"},
                        "spans": [
                            {
                                "spanId": s["span_id"],
                                "parentSpanId": s.get("parent_id") or "",
                                "name": s["name"],
                                "attributes": [
                                    {"key": k, "value": {"stringValue": str(v)}}
                                    for k, v in s.get("attributes", {}).items()
                                ],
                            }
                            for s in spans
                        ],
                    }
                ],
            }
        ]
    }


def validate_otel(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check a payload against the minimal OTLP shape the OTel-export probe requires.

    Returns (ok, problems). Every span must carry non-empty spanId + name; the
    resource must declare service.name.
    """
    problems: list[str] = []
    resource_spans = payload.get("resourceSpans")
    if not isinstance(resource_spans, list) or not resource_spans:
        return False, ["missing resourceSpans"]
    for rs in resource_spans:
        attrs = {a.get("key") for a in rs.get("resource", {}).get("attributes", [])}
        if "service.name" not in attrs:
            problems.append("resource missing service.name")
        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                if not span.get("spanId"):
                    problems.append("span missing spanId")
                if not span.get("name"):
                    problems.append("span missing name")
    return (not problems), problems
