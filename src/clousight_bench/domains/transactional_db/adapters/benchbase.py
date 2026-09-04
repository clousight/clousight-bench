"""BenchBase adapters for the transactional-db (OLTP) domain.

The SUT-connection abstraction for OLTP is BenchBase's ``dbtype`` + JDBC endpoint,
surfaced through the run ``Target``:

- ``BenchbaseLocalAdapter`` (``benchbase-local``): ``dbtype=sqlite`` — an embedded
  file database, no server. A provider-less reference that proves the pipeline
  without an external DB. (The real path still needs the BenchBase build; the
  offline path is the suite's ``mock_artifacts``.)
- ``JdbcEndpointAdapter`` (``jdbc-endpoint``): config-connect to an ALREADY-
  RUNNING database — ``dbtype`` (default ``postgres``) + endpoint host:port from
  the ``Target``. This is the "配置接入即可" path.

Cloud-managed RDBMS backends attach later as additional adapters on the same seam.

``java``/BenchBase are not Python deps: preflight checks the launcher is reachable
and fails loud with an actionable hint on the real path (skipped for mock runs).
"""

from __future__ import annotations

import os
import shutil
from typing import Any

from clousight_bench.core.plugin import ProviderAdapter


def _benchbase_launcher() -> str | None:
    """Locate the BenchBase launcher: ``benchbase`` on PATH or ``$BENCHBASE_HOME``."""
    found = shutil.which("benchbase")
    if found:
        return found
    home = os.environ.get("BENCHBASE_HOME", "")
    for rel in ("benchbase.jar", "benchbase"):
        candidate = os.path.join(home, rel) if home else ""
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


class _BenchbaseAdapterBase(ProviderAdapter):
    """Shared BenchBase adapter: preflight the tool; expose the dbtype to the suite."""

    db_type: str = "sqlite"

    def dbtype(self) -> str:
        """The BenchBase DB type this platform drives (target may override)."""
        return str(self.target.get("dbtype") or self.db_type)

    def _is_mock(self) -> bool:
        return str(self.target.get("mode", "")).lower() == "mock" or bool(self.target.get("mock"))

    def preflight(self, task: Any | None = None) -> Any:
        from clousight_bench.core import preflight as pf

        report = pf.PreflightReport()
        # Mock runs never touch the BenchBase tool — don't gate them on it.
        if self._is_mock():
            report.add(
                pf.Check(
                    "benchbase",
                    ok=True,
                    severity=pf.WARNING,
                    detail="mock run — BenchBase not required",
                )
            )
            return report
        if _benchbase_launcher() is None:
            report.add(
                pf.Check(
                    "benchbase",
                    ok=False,
                    severity=pf.CRITICAL,
                    detail="BenchBase launcher not found",
                    remediation="build BenchBase (Java); put `benchbase` on PATH or set BENCHBASE_HOME",
                )
            )
        else:
            report.add(pf.Check("benchbase", ok=True, severity=pf.CRITICAL, detail="launcher found"))
        return report


class BenchbaseLocalAdapter(_BenchbaseAdapterBase):
    """Embedded reference: BenchBase ``dbtype=sqlite``, no external database."""

    name = "benchbase-local"
    status = "reference"
    provider = None
    db_type = "sqlite"
    target_example: dict = {"scalefactor": 1, "terminals": 1, "time": 60}

    def execution_mode(self) -> str:
        # An embedded single-file DB is a simulated reference, never pooled with
        # numbers from a real production database.
        return "simulated"


class JdbcEndpointAdapter(_BenchbaseAdapterBase):
    """Config-connect to an already-running database (dbtype + JDBC endpoint)."""

    name = "jdbc-endpoint"
    status = "experimental"
    provider = None
    db_type = "postgres"
    target_example: dict = {
        "dbtype": "postgres",
        "endpoint": "127.0.0.1:5432",
        "credentials_ref": "env:PGPASSWORD",
        "scalefactor": 10,
        "terminals": 8,
        "time": 60,
    }
