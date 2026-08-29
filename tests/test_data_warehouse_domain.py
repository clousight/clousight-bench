"""The data-warehouse domain + duckdb-local reference adapter (TPC-DS slice 1)."""

from __future__ import annotations

import importlib.util

import pytest

from clousight_bench.core import preflight as pf
from clousight_bench.core.registry import load_domains
from clousight_bench.domains.data_warehouse import DataWarehouseDomain
from clousight_bench.domains.data_warehouse.adapters.duckdb_local import DuckDbLocalAdapter

_DUCKDB = importlib.util.find_spec("duckdb") is not None


def test_domain_loads_via_registry() -> None:
    domains = load_domains()
    assert "data-warehouse" in domains
    assert isinstance(domains["data-warehouse"], DataWarehouseDomain)


def test_domain_is_suite_first_no_tasks() -> None:
    assert DataWarehouseDomain().tasks() == {}


def test_domain_declares_duckdb_local() -> None:
    adapters = DataWarehouseDomain().adapters()
    assert set(adapters) == {"duckdb-local"}
    assert adapters["duckdb-local"] is DuckDbLocalAdapter


def test_duckdb_local_is_a_simulated_reference() -> None:
    adapter = DuckDbLocalAdapter()
    assert adapter.name == "duckdb-local"
    assert adapter.status == "reference"
    assert adapter.provider is None
    assert adapter.is_runnable()  # reference != skeleton
    assert adapter.execution_mode() == "simulated"


def test_module_imports_without_duckdb() -> None:
    # The domain + adapter modules must import cleanly even without the [tpcds]
    # extra (duckdb is imported lazily inside preflight only).
    import clousight_bench.domains.data_warehouse  # noqa: F401
    import clousight_bench.domains.data_warehouse.adapters.duckdb_local  # noqa: F401


def test_setup_teardown_are_noops() -> None:
    adapter = DuckDbLocalAdapter()
    assert adapter.setup() is None
    assert adapter.teardown() is None


@pytest.mark.skipif(not _DUCKDB, reason="requires the [tpcds] extra (duckdb)")
def test_preflight_passes_when_duckdb_and_tpcds_available() -> None:
    report = DuckDbLocalAdapter().preflight()
    assert isinstance(report, pf.PreflightReport)
    names = {c.name for c in report.checks}
    assert {"duckdb", "tpcds-extension"} <= names
    critical_failures = [c for c in report.checks if not c.ok and c.severity == pf.CRITICAL]
    assert not critical_failures, [c.line() for c in critical_failures]
