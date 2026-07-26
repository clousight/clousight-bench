"""VALIDATE: parse the request at the boundary, before provisioning.

Anything raised here is a user input error, not a benchmark result: the CLI
turns it into exit code 2 and no record is written, because a request we could
not even parse never measured anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from clousight_bench.core.canonical import CanonicalJSONError, canonical_json
from clousight_bench.core.errors import UserInputError
from clousight_bench.core.plugin import Task
from clousight_bench.core.redaction import redact
from clousight_bench.core.schema import RunSpec


class InvalidRunSpecError(UserInputError):
    """A RunSpec, its target, its params or its task config cannot be used."""


def _require_encodable(label: str, value: Mapping[str, Any]) -> None:
    try:
        payload = dict(value)
    except Exception as exc:  # noqa: BLE001 - malformed mappings are user input
        raise InvalidRunSpecError(
            f"{label} cannot be read as a mapping: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        canonical_json(redact(payload))
    except CanonicalJSONError as exc:
        raise InvalidRunSpecError(
            f"{label} is not canonically encodable: {exc}"
        ) from exc


def validate_run_spec(spec: RunSpec, task: Task) -> None:
    """Validate a resolved run request without touching external resources."""
    for field in ("domain", "task_id", "platform"):
        value = getattr(spec, field, None)
        if not isinstance(value, str) or not value.strip():
            raise InvalidRunSpecError(
                f"{field} must be a non-empty string, got {value!r}"
            )

    for field in ("target", "params"):
        value = getattr(spec, field, None)
        if not isinstance(value, Mapping):
            raise InvalidRunSpecError(
                f"{field} must be a mapping, got {type(value).__name__}"
            )
        _require_encodable(field, value)

    try:
        config = task.config(spec.params)
    except Exception as exc:  # noqa: BLE001 - a task rejecting params is user input
        raise InvalidRunSpecError(
            f"task {spec.task_id!r} rejected these params in config(): "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(config, Mapping):
        raise InvalidRunSpecError(
            f"task {spec.task_id!r} config() must return a mapping, "
            f"got {type(config).__name__}"
        )
    _require_encodable("task config", config)
