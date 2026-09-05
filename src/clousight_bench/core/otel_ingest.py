"""Ingest externally-produced OpenTelemetry spans as a v3 trajectory.

The reverse direction of the export story: a SUT (or any tool) instrumented
with standard OTel can hand its spans to the viewer/waterfall. Two input
shapes are accepted:

* **OTLP/JSON** — the protobuf JSON encoding (``{"resourceSpans": [...]}``),
  what a collector's file exporter or an SDK's OTLP-JSON dump produces;
* **flat JSONL** — one span object per line in the harness's own flat shape
  (already v3: passthrough + validation).

Every emitted span is schema-v3 validated; spans whose attributes carry no
semconv discriminator get ``csbench.ingested`` added (honest marker: we know
nothing about them beyond OTel shape). Backs ``csbench trace import``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clousight_bench.core.sut_span import DISCRIMINATOR_PREFIXES, is_v3_span, validate_span

_STATUS_CODES = {
    0: "UNSET",
    1: "OK",
    2: "ERROR",
    "STATUS_CODE_UNSET": "UNSET",
    "STATUS_CODE_OK": "OK",
    "STATUS_CODE_ERROR": "ERROR",
}


def _unwrap_value(value: dict[str, Any] | Any) -> Any:
    """OTLP AnyValue -> plain python (stringValue/intValue/... unwrapped)."""
    if not isinstance(value, dict):
        return value
    for key, cast in (
        ("stringValue", str),
        ("boolValue", bool),
        ("intValue", int),
        ("doubleValue", float),
    ):
        if key in value:
            return cast(value[key])
    if "arrayValue" in value:
        return [_unwrap_value(v) for v in value["arrayValue"].get("values", [])]
    return json.dumps(value)


def _attrs(otlp_attrs: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for entry in otlp_attrs or []:
        if isinstance(entry, dict) and "key" in entry:
            out[str(entry["key"])] = _unwrap_value(entry.get("value"))
    return out


def _from_otlp_span(span: dict[str, Any], resource_attrs: dict[str, Any]) -> dict[str, Any]:
    attributes = _attrs(span.get("attributes"))
    if not any(k.startswith(DISCRIMINATOR_PREFIXES) for k in attributes):
        attributes["csbench.ingested"] = "otlp"
    status_field = span.get("status")
    status_raw = status_field.get("code", 0) if isinstance(status_field, dict) else 0
    out = {
        "trace_id": str(span.get("traceId", "")).lower(),
        "span_id": str(span.get("spanId", "")).lower(),
        "parent_span_id": str(span.get("parentSpanId", "") or "").lower(),
        "name": str(span.get("name") or "span"),
        "start_unix_nano": int(span.get("startTimeUnixNano") or 0),
        "end_unix_nano": int(span.get("endTimeUnixNano") or 0),
        "status": _STATUS_CODES.get(status_raw, "UNSET"),
        "attributes": attributes,
    }
    if resource_attrs:
        out["resource"] = resource_attrs
    return out


def ingest_otlp_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """OTLP/JSON ``resourceSpans`` payload -> validated v3 span dicts."""
    spans: list[dict[str, Any]] = []
    for rs in payload.get("resourceSpans") or []:
        resource_attrs = _attrs((rs.get("resource") or {}).get("attributes"))
        for scope in rs.get("scopeSpans") or []:
            for span in scope.get("spans") or []:
                v3 = _from_otlp_span(span, resource_attrs)
                validate_span(v3)
                spans.append(v3)
    return spans


def ingest_file(path: Path) -> list[dict[str, Any]]:
    """Read *path* (OTLP JSON or flat JSONL) -> validated v3 spans.

    Raises ``ValueError`` with a per-line/per-span message on anything invalid —
    an import must be loud, never a silently mangled trajectory.
    """
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{") and '"resourceSpans"' in stripped[:2000]:
        return ingest_otlp_json(json.loads(text))
    spans = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            span = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: not JSON: {exc}") from exc
        if not isinstance(span, dict):
            raise ValueError(f"{path}:{lineno}: span line must be a JSON object")
        if not is_v3_span(span):
            raise ValueError(
                f"{path}:{lineno}: not a v3 (OTel-native) span — external imports must be v3 "
                "(legacy v2 is only accepted from the deployed agent-bundle path)"
            )
        attributes = span.get("attributes")
        if isinstance(attributes, dict) and not any(k.startswith(DISCRIMINATOR_PREFIXES) for k in attributes):
            attributes["csbench.ingested"] = "jsonl"
        validate_span(span)
        spans.append(span)
    return spans
