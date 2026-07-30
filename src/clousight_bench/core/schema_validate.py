"""Validate contract objects against the packaged JSON Schema files.

The authoritative contracts live as ``.schema.json`` under
``clousight_bench/resources/schemas``. When the optional ``jsonschema`` package
(``pip install clousight-bench[validate]``) is available we do a full Draft
2020-12 validation; otherwise we call the caller-supplied ``fallback`` (the
existing hand-written validator) so we still reject bad input without the dep.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from functools import lru_cache
from importlib import resources
from typing import Any


class SchemaValidationError(ValueError):
    """An instance did not match its published JSON Schema."""


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    fname = f"{name}.schema.json"
    try:
        text = (
            resources.files("clousight_bench.resources")
            .joinpath("schemas", fname)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise SchemaValidationError(f"no packaged schema {name!r}") from exc
    return json.loads(text)


def validate_against_schema(
    instance: Mapping[str, Any],
    schema_name: str,
    *,
    fallback: Callable[[Mapping[str, Any]], None] | None = None,
) -> None:
    """Validate ``instance`` against the named schema.

    With ``jsonschema`` installed, do a full Draft 2020-12 validation and raise
    ``SchemaValidationError`` (pointing at the offending JSON path) on the first
    error. Without it, delegate to ``fallback`` (the hand-written floor) so bad
    input is still rejected; if no fallback is given, the call is a no-op.
    """
    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError:
        if fallback is not None:
            fallback(instance)
        return
    schema = load_schema(schema_name)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        path = "/".join(str(p) for p in e.path) or "<root>"
        raise SchemaValidationError(f"{schema_name} invalid at {path}: {e.message}")
