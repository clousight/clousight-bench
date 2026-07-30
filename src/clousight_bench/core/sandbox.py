"""Workload/asset sandboxing (layers 1+2): path boundaries, URI policy, rlimits.

This does NOT provide strong isolation against a determined adversary (that needs
filesystem/network/process isolation, a later slice). It closes the largest
exploitation surface: path traversal, SSRF, and runaway processes.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class SandboxViolation(RuntimeError):
    """A workload/asset tried to escape its boundary or use a disallowed URI."""


def resolve_within(base: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``base``; raise if the real path escapes ``base``.

    Rejects absolute paths, ``..`` escapes, and symlink escapes (the target is
    resolved with symlinks followed before the boundary check).
    """
    base_real = Path(base).resolve()
    if Path(rel).is_absolute():
        raise SandboxViolation(f"absolute path not allowed: {rel!r}")
    target = (base_real / rel).resolve()
    if target != base_real and base_real not in target.parents:
        raise SandboxViolation(f"path {rel!r} escapes {base_real}")
    return target


_BLOCKED_HOSTNAMES = {"localhost", "metadata", "metadata.google.internal"}


def _is_blocked_host(host: str) -> bool:
    h = host.strip("[]").lower()
    if h in _BLOCKED_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return (
        ip.is_loopback or ip.is_link_local or ip.is_private
        or ip.is_reserved or ip.is_unspecified
    )


def validate_asset_uri(uri: str, *, allow_hosts: tuple[str, ...] = ()) -> None:
    """Remote-asset URI policy: https-only, no SSRF targets, optional host allowlist."""
    parsed = urlparse(uri)
    if parsed.scheme != "https":
        raise SandboxViolation(f"remote asset must use https, got {parsed.scheme!r}: {uri}")
    host = parsed.hostname or ""
    if not host:
        raise SandboxViolation(f"remote asset uri has no host: {uri}")
    if _is_blocked_host(host):
        raise SandboxViolation(f"remote asset host is not allowed (SSRF guard): {host}")
    if allow_hosts and host not in allow_hosts:
        raise SandboxViolation(f"remote asset host {host!r} not in allow-list {allow_hosts}")


@dataclass
class ResourceLimits:
    cpu_s: int | None = 1800          # RLIMIT_CPU seconds
    mem_bytes: int | None = 2 << 30   # RLIMIT_AS, 2 GiB
    fsize_bytes: int | None = 1 << 30  # RLIMIT_FSIZE, 1 GiB
    nofile: int | None = 1024         # RLIMIT_NOFILE

    @classmethod
    def from_target(cls, target: dict) -> ResourceLimits:
        """Read overrides from ``target['limits']``; a None/0 value disables a limit."""
        raw = dict(target.get("limits") or {})

        def _pick(key: str, default: int | None, *, scale: int = 1) -> int | None:
            if key not in raw:
                return default
            val = raw[key]
            if val is None or val == 0:
                return None
            return int(val) * scale

        return cls(
            cpu_s=_pick("cpu_s", cls.cpu_s),
            mem_bytes=_pick("mem_mb", cls.mem_bytes, scale=1 << 20),
            fsize_bytes=_pick("fsize_mb", cls.fsize_bytes, scale=1 << 20),
            nofile=_pick("nofile", cls.nofile),
        )


def posix_rlimit_preexec(limits: ResourceLimits) -> Callable[[], None] | None:
    """A preexec_fn applying setrlimit in the child before exec, or None off-POSIX."""
    if os.name != "posix":
        return None
    import resource

    plan: list[tuple[int, int]] = []
    mapping = [
        ("RLIMIT_CPU", limits.cpu_s),
        ("RLIMIT_AS", limits.mem_bytes),
        ("RLIMIT_FSIZE", limits.fsize_bytes),
        ("RLIMIT_NOFILE", limits.nofile),
    ]
    for name, value in mapping:
        if value is None:
            continue
        const = getattr(resource, name, None)
        if const is None:
            logger.warning("sandbox: %s unsupported on this platform; skipping", name)
            continue
        plan.append((const, int(value)))

    def _apply() -> None:  # runs in the child, after fork, before exec
        import resource as _r

        for const, value in plan:
            try:
                _r.setrlimit(const, (value, value))
            except (ValueError, OSError):
                pass  # cannot raise our limit above a hard cap; skip

    return _apply
