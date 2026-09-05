"""SUT trajectory span schema and validator (v3 OTel-native; legacy v2 accepted).

A *span* is one observable unit of the evaluated activity — an LLM inference
call, a tool invocation, a benchmark phase, a query.  Spans form a forest and
the full set for one run is written as NDJSON into ``trajectory.jsonl`` in the
``RawArtifacts`` directory.

**Schema v3 (OTel-native flat span, the same shape the harness trace files
use)** — required keys::

    {trace_id, span_id, parent_span_id, name,
     start_unix_nano, end_unix_nano, status, attributes}

- ``trace_id`` is 32 hex chars, ``span_id`` 16 hex chars (W3C Trace Context);
  ``parent_span_id`` is ``""``/``None`` for a root or 16 hex chars.
- times are integer nanoseconds, ``end >= start``.
- ``status`` is one of ``V3_STATUSES`` (OTel status-code names).
- ``attributes`` is a flat dict (≤ ``MAX_ATTRS_BYTES`` JSON-encoded) carrying
  the span's semantics via OTel semantic-convention names; at least one key
  must start with a ``DISCRIMINATOR_PREFIXES`` namespace (``gen_ai.`` for
  LLM/tool activity, ``db.`` for queries, ``csbench.`` for phases/streams).

**Legacy v2** (``{span_id, trace_id, parent_id, name, kind, t_start, t_end,
status, attrs}`` with ``kind`` in ``SPAN_KINDS``, seconds floats, lowercase
statuses) is still accepted — the deployed agent-bundle protocol emits it; the
viewer normalizes both.  New span sources must emit v3.

``validate_span(d)`` is a pure, side-effect-free predicate that raises
``ValueError`` with a clear, per-failure message.  Safe to call in hot loops.
"""

from __future__ import annotations

import json
from typing import Any

# --- legacy v2 vocabulary (deployed agent-bundle protocol) --------------------
SPAN_KINDS: tuple[str, ...] = ("tool_call", "llm_call")
SPAN_STATUSES: tuple[str, ...] = ("ok", "error")

# --- v3 vocabulary (OTel-native) ---------------------------------------------
V3_STATUSES: tuple[str, ...] = ("OK", "ERROR", "UNSET")
DISCRIMINATOR_PREFIXES: tuple[str, ...] = ("gen_ai.", "db.", "csbench.")

MAX_ATTRS_BYTES: int = 65536

_V2_REQUIRED: frozenset[str] = frozenset(
    {"span_id", "trace_id", "parent_id", "name", "kind", "t_start", "t_end", "status", "attrs"}
)
_V3_REQUIRED: frozenset[str] = frozenset(
    {
        "span_id",
        "trace_id",
        "parent_span_id",
        "name",
        "start_unix_nano",
        "end_unix_nano",
        "status",
        "attributes",
    }
)


def _require_hex(value: Any, field: str, length: int) -> None:
    if not isinstance(value, str) or len(value) != length:
        raise ValueError(f"span {field!r} must be a {length}-hex-char str, got {value!r}")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"span {field!r} must be hex, got {value!r}") from exc


def _check_attrs_dict(attrs: Any, field: str) -> None:
    if not isinstance(attrs, dict):
        raise ValueError(f"span {field!r} must be a dict, got {type(attrs).__name__!r}")
    try:
        attrs_bytes = json.dumps(attrs).encode()
    except TypeError as exc:
        raise ValueError(f"span {field!r} is not JSON-serializable: {exc}") from exc
    if len(attrs_bytes) > MAX_ATTRS_BYTES:
        raise ValueError(
            f"span {field!r} JSON encoding is {len(attrs_bytes)} bytes, "
            f"exceeding MAX_ATTRS_BYTES={MAX_ATTRS_BYTES}"
        )


def is_v3_span(d: dict[str, Any]) -> bool:
    """Shape sniff: v3 carries ``attributes``/``start_unix_nano``; v2 ``attrs``/``t_start``."""
    return "attributes" in d or "start_unix_nano" in d


def _validate_v3(d: dict[str, Any]) -> None:
    missing = _V3_REQUIRED - d.keys()
    if missing:
        raise ValueError(f"v3 span missing required key(s): {sorted(missing)!r}")

    _require_hex(d["trace_id"], "trace_id", 32)
    _require_hex(d["span_id"], "span_id", 16)
    parent = d["parent_span_id"]
    if parent not in (None, ""):
        _require_hex(parent, "parent_span_id", 16)

    if not isinstance(d["name"], str) or not d["name"]:
        raise ValueError(f"span 'name' must be a non-empty str, got {d['name']!r}")

    for key in ("start_unix_nano", "end_unix_nano"):
        v = d[key]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"span {key!r} must be an int (nanoseconds), got {type(v).__name__!r}")
    if d["end_unix_nano"] < d["start_unix_nano"]:
        raise ValueError(
            f"span 'end_unix_nano' ({d['end_unix_nano']!r}) must be >= "
            f"'start_unix_nano' ({d['start_unix_nano']!r})"
        )

    if d["status"] not in V3_STATUSES:
        raise ValueError(f"span 'status' must be one of {V3_STATUSES!r}, got {d['status']!r}")

    attrs = d["attributes"]
    _check_attrs_dict(attrs, "attributes")
    if not any(isinstance(k, str) and k.startswith(DISCRIMINATOR_PREFIXES) for k in attrs):
        raise ValueError(
            "v3 span 'attributes' must carry at least one semconv discriminator key "
            f"(a name starting with one of {DISCRIMINATOR_PREFIXES!r})"
        )


def _validate_v2(d: dict[str, Any]) -> None:
    missing = _V2_REQUIRED - d.keys()
    if missing:
        raise ValueError(f"span missing required key(s): {sorted(missing)!r}")

    trace_id = d["trace_id"]
    if not isinstance(trace_id, str) or not trace_id:
        raise ValueError(f"span 'trace_id' must be a non-empty str, got {trace_id!r}")

    kind = d["kind"]
    if kind not in SPAN_KINDS:
        raise ValueError(f"span 'kind' must be one of {SPAN_KINDS!r}, got {kind!r}")

    t_start = d["t_start"]
    t_end = d["t_end"]
    for label, v in (("t_start", t_start), ("t_end", t_end)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"span {label!r} must be numeric (int or float), got {type(v).__name__!r}")
    if t_end < t_start:
        raise ValueError(f"span 't_end' ({t_end!r}) must be >= 't_start' ({t_start!r})")

    status = d["status"]
    if status not in SPAN_STATUSES:
        raise ValueError(f"span 'status' must be one of {SPAN_STATUSES!r}, got {status!r}")
    if status == "error" and "error" in d and not isinstance(d["error"], str):
        raise ValueError(f"span 'error' must be a str when present, got {type(d['error']).__name__!r}")

    _check_attrs_dict(d["attrs"], "attrs")

    parent_id = d["parent_id"]
    if parent_id is not None:
        if not isinstance(parent_id, str) or not parent_id:
            raise ValueError(f"span 'parent_id' must be None or a non-empty string, got {parent_id!r}")


def validate_span(d: Any) -> None:
    """Raise ``ValueError`` unless *d* is a valid v3 (OTel-native) or legacy v2 span."""
    if not isinstance(d, dict):
        raise ValueError(f"span must be a dict, got {type(d).__name__!r}")
    if is_v3_span(d):
        _validate_v3(d)
    else:
        _validate_v2(d)
