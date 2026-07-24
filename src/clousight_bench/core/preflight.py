"""Preflight: check prerequisites BEFORE provisioning, fail fast with guidance.

The lifecycle gains a gate in front of it:

    PREFLIGHT -> resolve -> setup -> execute -> teardown -> record

Rather than discovering half-way through a run that AWS credentials are missing
or the mock universe is unreachable, the orchestrator asks the adapter to
self-check first. Every check is one `Check` with a severity: a failing
`critical` check aborts the run before any resource is provisioned; a failing
`warning` is surfaced but does not block.

This module holds the reusable check *functions* -- the single source of truth
shared by `ProviderAdapter.preflight()` (used by `csbench run`) and
`csbench doctor`. Checks only inspect the environment; they never read or store
a secret value.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from typing import Any
from urllib import request

from clousight_bench.core.credentials import (
    PROVIDER_CREDENTIALS,
    infer_provider,
    resolve_credentials,
)

CRITICAL = "critical"
WARNING = "warning"


@dataclass
class Check:
    name: str
    ok: bool
    severity: str = CRITICAL
    detail: str = ""
    remediation: str = ""

    def mark(self) -> str:
        if self.ok:
            return "\u2713"  # ✓
        return "\u2717" if self.severity == CRITICAL else "!"  # ✗ / !

    def line(self) -> str:
        tail = self.detail or self.remediation
        return f"{self.mark()} {self.name}{' — ' + tail if tail else ''}"


@dataclass
class PreflightReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, *checks: Check | None) -> PreflightReport:
        self.checks.extend(c for c in checks if c is not None)
        return self

    @property
    def ok(self) -> bool:
        """True iff no CRITICAL check failed (warnings don't block)."""
        return all(c.ok for c in self.checks if c.severity == CRITICAL)

    @property
    def critical_failures(self) -> list[Check]:
        return [c for c in self.checks if c.severity == CRITICAL and not c.ok]

    def format(self) -> str:
        return "\n".join(c.line() for c in self.checks)

    def summary(self) -> str:
        fails = self.critical_failures
        if not fails:
            return "all critical checks passed"
        return "; ".join(f"{c.name}: {c.remediation or c.detail or 'failed'}" for c in fails)


# --- Reusable check functions (single source of truth) ----------------------

def credential_check(target: dict[str, Any], platform: str | None) -> Check:
    """Are credentials resolvable from the cloud's default chain?

    Provider-less adapters (e.g. local-sim) need none -> a passing warning.
    """
    provider = infer_provider(target, platform)
    if provider is None:
        return Check("credentials", ok=True, severity=WARNING,
                     detail="no cloud credentials required")
    res = resolve_credentials(target, platform=platform)
    if res.ok:
        return Check("credentials", ok=True, severity=CRITICAL,
                     detail=f"via {res.source} ({res.identity_hint})")
    return Check("credentials", ok=False, severity=CRITICAL,
                 detail=f"{provider}: not resolvable", remediation=res.remediation)


def sdk_check(target: dict[str, Any], platform: str | None) -> Check | None:
    """Is the provider SDK importable? Warning only -- needed to run real tasks,
    not to preflight. Returns None for provider-less adapters."""
    provider = infer_provider(target, platform)
    if provider is None:
        return None
    sdk = PROVIDER_CREDENTIALS[provider]["sdk_module"]
    if importlib.util.find_spec(sdk) is not None:
        return Check(f"sdk:{sdk}", ok=True, severity=WARNING, detail="importable")
    return Check(f"sdk:{sdk}", ok=False, severity=WARNING,
                 detail="not installed", remediation=f"pip install {sdk} (only needed for real runs)")


def mock_reachable_check(url: str) -> Check:
    """The pinned tool universe must be reachable BY THE CLOUD RUNTIME.

    localhost / unset are hard fails; unreachable-from-here is a warning (a
    firewall may still let the cloud in, but it usually means misconfig).
    """
    url = (url or "").strip()
    if not url:
        return Check("mock_base_url", ok=False, severity=CRITICAL,
                     detail="not set",
                     remediation="expose the mock server publicly and set target.mock_base_url")
    if url.startswith(("http://127.", "http://localhost", "https://localhost", "http://0.0.0.0")):
        return Check("mock_base_url", ok=False, severity=CRITICAL,
                     detail=f"{url} is localhost",
                     remediation="a cloud runtime cannot reach localhost; use a tunnel or cloud function")
    try:
        with request.urlopen(f"{url.rstrip('/')}/health", timeout=5) as resp:
            healthy = 200 <= resp.status < 300
        if healthy:
            return Check("mock_base_url", ok=True, severity=CRITICAL, detail=f"{url} /health ok")
        return Check("mock_base_url", ok=False, severity=WARNING,
                     detail=f"{url} /health non-2xx",
                     remediation="mock server responded but not healthy")
    except Exception as exc:  # noqa: BLE001 - report, never crash preflight
        return Check("mock_base_url", ok=False, severity=WARNING,
                     detail=f"{url} unreachable from here ({type(exc).__name__})",
                     remediation="confirm it is publicly reachable by the cloud runtime")
