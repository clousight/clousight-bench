"""SUT trajectory span schema and validator.

A *span* is one observable unit of an agent's execution trace — either an LLM
inference call or a tool invocation.  Spans form a forest (a ``parent_id`` of
``None`` marks a root span); the full set for one benchmark run is written as
NDJSON into ``trajectory.jsonl`` in the ``RawArtifacts`` directory.

``SPAN_KINDS`` is the exhaustive set of allowed ``kind`` values.  Sub-project C
(the trajectory viewer) renders each kind differently, so the set must be
stable and finite.

``SPAN_STATUSES`` is the exhaustive set of allowed ``status`` values.

``MAX_ATTRS_BYTES`` is the upper bound (inclusive) on the UTF-8 JSON encoding
of the ``attrs`` dict.

``validate_span(d)`` is a pure, side-effect-free predicate that raises
``ValueError`` with a clear, per-failure message.  It is safe to call in hot
loops (no I/O).

Schema v2 required keys::

    {span_id, trace_id, parent_id, name, kind, t_start, t_end, status, attrs}

Optional key: ``error: str`` (only meaningful when ``status == "error"``).
"""

from __future__ import annotations

import json
from typing import Any

SPAN_KINDS: tuple[str, ...] = ("tool_call", "llm_call")
SPAN_STATUSES: tuple[str, ...] = ("ok", "error")

MAX_ATTRS_BYTES: int = 65536

_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"span_id", "trace_id", "parent_id", "name", "kind", "t_start", "t_end", "status", "attrs"}
)


def validate_span(d: Any) -> None:
    """Raise ``ValueError`` if *d* is not a valid SUT trajectory span (schema v2).

    A valid span is a ``dict`` with at least the keys
    ``{span_id, trace_id, parent_id, name, kind, t_start, t_end, status, attrs}`` where:

    - ``trace_id`` is a non-empty ``str``
    - ``kind`` is one of ``SPAN_KINDS``
    - ``t_start`` and ``t_end`` are numeric (``int`` or ``float``, not ``bool``)
    - ``t_end >= t_start``
    - ``status`` is one of ``SPAN_STATUSES``
    - if ``status == "error"`` and ``"error"`` key is present, its value must be ``str``
    - ``attrs`` is a ``dict`` whose JSON encoding fits in ``MAX_ATTRS_BYTES`` bytes
    - ``parent_id`` is ``None`` (root span) or a NON-EMPTY ``str``

    Extra keys are allowed; they are ignored.
    """
    if not isinstance(d, dict):
        raise ValueError(f"span must be a dict, got {type(d).__name__!r}")

    missing = _REQUIRED_KEYS - d.keys()
    if missing:
        raise ValueError(f"span missing required key(s): {sorted(missing)!r}")

    # --- trace_id ---
    trace_id = d["trace_id"]
    if not isinstance(trace_id, str) or not trace_id:
        raise ValueError(f"span 'trace_id' must be a non-empty str, got {trace_id!r}")

    # --- kind ---
    kind = d["kind"]
    if kind not in SPAN_KINDS:
        raise ValueError(f"span 'kind' must be one of {SPAN_KINDS!r}, got {kind!r}")

    # --- t_start / t_end: bool check BEFORE numeric check ---
    t_start = d["t_start"]
    t_end = d["t_end"]
    if isinstance(t_start, bool):
        raise ValueError("span 't_start' must be numeric (int or float), got bool")
    if isinstance(t_end, bool):
        raise ValueError("span 't_end' must be numeric (int or float), got bool")
    if not isinstance(t_start, (int, float)):
        raise ValueError(f"span 't_start' must be numeric (int or float), got {type(t_start).__name__!r}")
    if not isinstance(t_end, (int, float)):
        raise ValueError(f"span 't_end' must be numeric (int or float), got {type(t_end).__name__!r}")
    if t_end < t_start:
        raise ValueError(f"span 't_end' ({t_end!r}) must be >= 't_start' ({t_start!r})")

    # --- status ---
    status = d["status"]
    if status not in SPAN_STATUSES:
        raise ValueError(f"span 'status' must be one of {SPAN_STATUSES!r}, got {status!r}")
    if status == "error" and "error" in d:
        if not isinstance(d["error"], str):
            raise ValueError(f"span 'error' must be a str when present, got {type(d['error']).__name__!r}")

    # --- attrs ---
    attrs = d["attrs"]
    if not isinstance(attrs, dict):
        raise ValueError(f"span 'attrs' must be a dict, got {type(attrs).__name__!r}")
    try:
        attrs_bytes = json.dumps(attrs).encode()
    except TypeError as exc:
        raise ValueError(f"span 'attrs' is not JSON-serializable: {exc}") from exc
    if len(attrs_bytes) > MAX_ATTRS_BYTES:
        raise ValueError(
            f"span 'attrs' JSON encoding is {len(attrs_bytes)} bytes, "
            f"exceeding MAX_ATTRS_BYTES={MAX_ATTRS_BYTES}"
        )

    # --- parent_id ---
    parent_id = d["parent_id"]
    if parent_id is not None:
        if not isinstance(parent_id, str):
            raise ValueError(
                f"span 'parent_id' must be None or a non-empty string, got {type(parent_id).__name__!r}"
            )
        if not parent_id:
            raise ValueError("span 'parent_id' must be None or a non-empty string, got empty string")
