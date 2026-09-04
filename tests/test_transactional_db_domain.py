"""The transactional-db (OLTP) domain + BenchBase adapters."""

from __future__ import annotations

from clousight_bench.core import preflight as pf
from clousight_bench.core.registry import load_domains
from clousight_bench.domains.transactional_db import TransactionalDbDomain
from clousight_bench.domains.transactional_db.adapters.benchbase import (
    BenchbaseLocalAdapter,
    JdbcEndpointAdapter,
)


def test_domain_loads_via_registry() -> None:
    domains = load_domains()
    assert "transactional-db" in domains
    assert isinstance(domains["transactional-db"], TransactionalDbDomain)


def test_domain_is_suite_first_no_tasks() -> None:
    # Single rail: domains no longer declare tasks at all.
    assert not hasattr(TransactionalDbDomain(), "tasks")


def test_domain_declares_both_platforms() -> None:
    adapters = TransactionalDbDomain().adapters()
    assert set(adapters) == {"benchbase-local", "jdbc-endpoint"}


def test_local_is_a_simulated_sqlite_reference() -> None:
    a = BenchbaseLocalAdapter()
    assert a.name == "benchbase-local"
    assert a.status == "reference"
    assert a.provider is None
    assert a.execution_mode() == "simulated"
    assert a.dbtype() == "sqlite"


def test_endpoint_dbtype_defaults_postgres_and_target_overrides() -> None:
    assert JdbcEndpointAdapter().dbtype() == "postgres"
    assert JdbcEndpointAdapter({"dbtype": "mysql"}).dbtype() == "mysql"
    assert JdbcEndpointAdapter.status == "experimental"


def test_preflight_passes_in_mock_without_the_tool() -> None:
    report = BenchbaseLocalAdapter({"mode": "mock"}).preflight()
    assert isinstance(report, pf.PreflightReport)
    assert not [c for c in report.checks if not c.ok and c.severity == pf.CRITICAL]


def test_preflight_fails_loud_without_tool_on_real_run() -> None:
    import shutil

    if shutil.which("benchbase"):
        return
    report = BenchbaseLocalAdapter({"mode": "runtime"}).preflight()
    crit = [c for c in report.checks if not c.ok and c.severity == pf.CRITICAL]
    assert crit and "benchbase" in crit[0].name.lower()
    assert "BENCHBASE_HOME" in crit[0].remediation or "PATH" in crit[0].remediation
