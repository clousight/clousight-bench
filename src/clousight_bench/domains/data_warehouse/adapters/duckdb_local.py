"""DuckDB single-node local reference adapter (the provider-less reference).

Proves the data-warehouse pipeline end-to-end WITHOUT any cloud account: an
in-process DuckDB engine with the ``tpcds`` extension, which can generate the
TPC-DS schema + data at a chosen scale factor and run the query set. Because it
is a single-node local engine, its numbers are ``simulated`` -- they must never
pool with live cloud data.

The cloud data-warehouse adapters (BigQuery / Redshift / MaxCompute ...) will
implement the same interface against live platforms in later tasks; they must
NOT re-implement suite or scoring logic.

Target keys: scale_factor (TPC-DS SF, GB of raw data at SF1).

``duckdb`` is imported lazily inside ``preflight`` so this module imports
cleanly without the ``[tpcds]`` extra installed.
"""

from __future__ import annotations

from clousight_bench.core.plugin import ProviderAdapter, Task


class DuckDbLocalAdapter(ProviderAdapter):
    name = "duckdb-local"
    status = "reference"
    provider = None
    target_example: dict = {"scale_factor": 1}

    def execution_mode(self) -> str:
        # Single-node local reference engine: its numbers never pool with live
        # cloud data.
        return "simulated"

    def setup(self) -> None:
        """No provisioning: the engine is in-process. No-op."""

    def teardown(self) -> None:
        """Nothing to release. No-op."""

    def preflight(self, task: Task | None = None) -> object:
        """Check the local engine is usable BEFORE a run.

        No cloud credentials are involved, so we do NOT call the default
        credential / SDK checks. Two checks, both CRITICAL:

        1. ``duckdb`` is importable.
        2. ``INSTALL tpcds; LOAD tpcds`` succeeds (the TPC-DS extension the
           suite needs to generate the schema/data and run queries).

        Both failures carry an actionable install hint.
        """
        from clousight_bench.core import preflight as pf

        report = pf.PreflightReport()
        hint = "pip install clousight-bench[tpcds]"

        try:
            import duckdb  # noqa: PLC0415 - lazy so the module imports without the extra
        except Exception as exc:  # noqa: BLE001 - report, never crash preflight
            report.add(
                pf.Check(
                    "duckdb",
                    ok=False,
                    severity=pf.CRITICAL,
                    detail=f"not importable ({type(exc).__name__})",
                    remediation=hint,
                )
            )
            return report

        report.add(pf.Check("duckdb", ok=True, severity=pf.CRITICAL, detail="importable"))

        try:
            con = duckdb.connect()
            try:
                con.execute("INSTALL tpcds; LOAD tpcds;")
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001 - report, never crash preflight
            report.add(
                pf.Check(
                    "tpcds-extension",
                    ok=False,
                    severity=pf.CRITICAL,
                    detail=f"INSTALL/LOAD tpcds failed ({type(exc).__name__}: {exc})",
                    remediation=hint,
                )
            )
            return report

        report.add(
            pf.Check("tpcds-extension", ok=True, severity=pf.CRITICAL, detail="INSTALL/LOAD tpcds ok")
        )
        return report
