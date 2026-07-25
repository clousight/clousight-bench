"""Keep credentials and machine identity out of records and fingerprints.

Two different jobs live here. ``redact`` scrubs values whose *key* looks like a
secret, and runs before anything is hashed or written. ``find_identity_leaks``
is the last line of defence: right before a record is persisted it looks for a
string that is exactly this machine's username, hostname or FQDN, because those
identify the operator rather than the benchmark.
"""
from __future__ import annotations

import getpass
import socket
from typing import Any

SECRET_HINTS: tuple[str, ...] = (
    "key",
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "authorization",
)
REDACTED = "<redacted>"


class SensitiveDataError(RuntimeError):
    """A payload about to be persisted still carries identifying data."""


def redact(value: Any) -> Any:
    """Return a copy with secret-looking mapping values replaced."""
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if any(hint in name.lower() for hint in SECRET_HINTS):
                clean[name] = REDACTED
            else:
                clean[name] = redact(item)
        return clean
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def identity_values() -> tuple[str, ...]:
    """This machine's operator-identifying strings, best effort."""
    found: list[str] = []
    for probe in (getpass.getuser, socket.gethostname, socket.getfqdn):
        try:
            value = probe()
        except Exception:  # noqa: BLE001 - identity probing must never break a run
            continue
        if isinstance(value, str) and len(value) >= 3:
            found.append(value)
    return tuple(dict.fromkeys(found))


def find_identity_leaks(
    payload: Any, identities: tuple[str, ...] | None = None
) -> list[str]:
    """Paths whose string value is exactly one of ``identities``."""
    known = identity_values() if identities is None else identities
    if not known:
        return []
    hits: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                walk(item, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")
        elif isinstance(node, str) and node in known:
            hits.append(path)

    walk(payload, "$")
    return hits
