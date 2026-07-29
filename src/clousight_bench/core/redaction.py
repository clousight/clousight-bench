"""Keep credentials and machine identity out of records and fingerprints.

Two different jobs live here. ``redact`` scrubs values whose *key* looks like a
secret, and runs before anything is hashed or written. ``find_identity_leaks``
is the last line of defence: right before a record is persisted it looks for a
string that is exactly this machine's username, hostname or FQDN, because those
identify the operator rather than the benchmark.
"""
from __future__ import annotations

import getpass
import re
import socket
from collections.abc import Callable
from functools import lru_cache
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
    return _identity_values_cached(getpass.getuser, socket.gethostname, socket.getfqdn)


@lru_cache(maxsize=8)
def _identity_values_cached(
    user_probe: Callable[[], str],
    host_probe: Callable[[], str],
    fqdn_probe: Callable[[], str],
) -> tuple[str, ...]:
    found: list[str] = []
    for probe in (user_probe, host_probe, fqdn_probe):
        try:
            value = probe()
        except Exception:  # noqa: BLE001 - identity probing must never break a run
            continue
        if isinstance(value, str) and len(value) >= 3:
            found.append(value)
    return tuple(dict.fromkeys(found))


def scrub_identity_text(text: str, identities: tuple[str, ...] | None = None) -> str:
    """Remove operator identities *embedded* in free text.

    ``find_identity_leaks`` only sees a value that IS an identity; an error
    message says ``/home/alice/results: permission denied``, which carries the
    same identity as a substring. Longest first, so a hostname that contains a
    username is replaced as one unit.
    """
    known = identity_values() if identities is None else identities
    for value in sorted(known, key=len, reverse=True):
        if not value:
            continue
        if len(value) <= 4 and value.isalnum():
            text = re.sub(
                rf"(?<!\w){re.escape(value)}(?!\w)",
                lambda _: REDACTED,
                text,
            )
        elif value in text:
            text = text.replace(value, REDACTED)
    return text


def scrub_identities(value: Any, identities: tuple[str, ...] | None = None) -> Any:
    """Return a copy of ``value`` with every embedded identity replaced."""
    known = identity_values() if identities is None else identities
    if not known:
        return value

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: walk(item) for key, item in node.items()}
        if isinstance(node, (list, tuple)):
            return [walk(item) for item in node]
        if isinstance(node, str):
            return scrub_identity_text(node, known)
        return node

    return walk(value)


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
