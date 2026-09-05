"""YCSB adapters for the key-value domain.

The SUT-connection abstraction for KV is YCSB's own *binding* + endpoint,
surfaced through the run ``Target``:

- ``YcsbLocalAdapter`` (``ycsb-local``): ``binding=basic`` — YCSB's in-memory
  no-op datastore. A provider-less reference that proves the pipeline without any
  external service. (The real path still needs the YCSB tool; the offline path is
  the suite's ``mock_artifacts``.)
- ``YcsbEndpointAdapter`` (``ycsb-endpoint``): config-connect to an ALREADY-
  RUNNING service — ``binding`` (default ``redis``) + endpoint host/port from the
  ``Target``. This is the "配置接入即可" path.

Cloud-managed KV backends attach later as additional adapters on the same seam
(a binding + a provisioned endpoint).

``java``/YCSB are not Python deps: preflight checks the tool is reachable and
fails loud with an actionable hint on the real path; nothing is imported here.
"""

from __future__ import annotations

import os
import shutil
from typing import Any

from clousight_bench.core.plugin import ProviderAdapter


def _ycsb_binary() -> str | None:
    """Locate the YCSB launcher: ``ycsb`` on PATH or ``$YCSB_HOME/bin/ycsb``."""
    found = shutil.which("ycsb")
    if found:
        return found
    home = os.environ.get("YCSB_HOME", "")
    candidate = os.path.join(home, "bin", "ycsb") if home else ""
    if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return None


class _YcsbAdapterBase(ProviderAdapter):
    """Shared YCSB adapter: preflight the tool; expose the binding to the suite."""

    ycsb_binding: str = "basic"

    def binding(self) -> str:
        """The YCSB DB binding this platform drives (target may override)."""
        return str(self.target.get("binding") or self.ycsb_binding)

    def _is_mock(self) -> bool:
        return str(self.target.get("mode", "")).lower() == "mock" or bool(self.target.get("mock"))

    def preflight(self, task: Any | None = None) -> Any:
        from clousight_bench.core import preflight as pf

        report = pf.PreflightReport()
        # Mock runs never touch the YCSB tool — don't gate them on it.
        if self._is_mock():
            report.add(pf.Check("ycsb", ok=True, severity=pf.WARNING, detail="mock run — YCSB not required"))
            return report
        if _ycsb_binary() is None:
            report.add(
                pf.Check(
                    "ycsb",
                    ok=False,
                    severity=pf.CRITICAL,
                    detail="YCSB launcher not found",
                    remediation="install YCSB (Java >= 11); put `ycsb` on PATH or set YCSB_HOME",
                )
            )
        else:
            report.add(pf.Check("ycsb", ok=True, severity=pf.CRITICAL, detail="launcher found"))
            report.add(pf.java_version_check("ycsb:java", min_major=11, hint="YCSB needs Java >= 11 on PATH"))
        endpoint = str(self.target.get("endpoint") or "")
        if endpoint:
            binding = str(self.target.get("binding") or self.ycsb_binding)
            if binding == "redis":
                # Protocol-level probe: PING -> +PONG / -NOAUTH both prove a live
                # RESP service (no password is ever sent by the probe).
                report.add(pf.resp_ping_check("ycsb:endpoint", endpoint))
            else:
                report.add(pf.tcp_reachable_check("ycsb:endpoint", endpoint))
        return report


class YcsbLocalAdapter(_YcsbAdapterBase):
    """In-memory reference: YCSB ``binding=basic``, no external datastore."""

    name = "ycsb-local"
    status = "reference"
    provider = None
    ycsb_binding = "basic"
    target_example: dict = {"workload": "workloada", "recordcount": 10000, "operationcount": 10000}

    def execution_mode(self) -> str:
        # binding=basic is an in-memory no-op DB: a simulated reference, never
        # pooled with numbers from a real datastore.
        return "simulated"


class YcsbEndpointAdapter(_YcsbAdapterBase):
    """Config-connect to an already-running KV service (binding + endpoint)."""

    name = "ycsb-endpoint"
    status = "experimental"
    provider = None
    ycsb_binding = "redis"
    target_example: dict = {
        "binding": "redis",
        "endpoint": "127.0.0.1:6379",
        "workload": "workloada",
        "recordcount": 10000,
        "operationcount": 10000,
    }
