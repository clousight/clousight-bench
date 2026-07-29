"""Canonical JSON encoding and SHA-256 digests.

Every fingerprint and every record digest is computed from the same encoding,
so two runs that mean the same thing hash identically on any machine and in
any Python version: UTF-8, object keys sorted, no insignificant whitespace,
NaN/Infinity rejected, and deterministic scalar encoding.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any


class CanonicalJSONError(ValueError):
    """A value cannot be encoded as canonical JSON."""


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise CanonicalJSONError(f"non-finite float is not canonical: {value!r}")
        return 0.0 if value == 0.0 else value
    raise CanonicalJSONError(
        f"unsupported type for canonical JSON: {type(value).__name__}"
    )


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalJSONError(
                    f"object keys must be strings, got {type(key).__name__}: {key!r}"
                )
            out[key] = _canonicalize(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    return _scalar(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: Any) -> str:
    blob = canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()
