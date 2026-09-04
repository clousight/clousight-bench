"""The key-value domain + YCSB adapters (SUT-connection abstraction)."""

from __future__ import annotations

from clousight_bench.core import preflight as pf
from clousight_bench.core.registry import load_domains
from clousight_bench.domains.key_value import KeyValueDomain
from clousight_bench.domains.key_value.adapters.ycsb import YcsbEndpointAdapter, YcsbLocalAdapter


def test_domain_loads_via_registry() -> None:
    domains = load_domains()
    assert "key-value" in domains
    assert isinstance(domains["key-value"], KeyValueDomain)


def test_domain_is_suite_first_no_tasks() -> None:
    # Single rail: domains no longer declare tasks at all.
    assert not hasattr(KeyValueDomain(), "tasks")


def test_domain_declares_both_ycsb_platforms() -> None:
    adapters = KeyValueDomain().adapters()
    assert set(adapters) == {"ycsb-local", "ycsb-endpoint"}


def test_local_is_a_simulated_basic_binding_reference() -> None:
    a = YcsbLocalAdapter()
    assert a.name == "ycsb-local"
    assert a.status == "reference"
    assert a.provider is None
    assert a.execution_mode() == "simulated"
    assert a.binding() == "basic"


def test_endpoint_binding_defaults_redis_and_target_overrides() -> None:
    assert YcsbEndpointAdapter().binding() == "redis"
    assert YcsbEndpointAdapter({"binding": "mongodb"}).binding() == "mongodb"
    assert YcsbEndpointAdapter.status == "experimental"


def test_preflight_passes_in_mock_without_the_tool() -> None:
    # A mock run must never be gated on the YCSB tool being installed.
    report = YcsbLocalAdapter({"mode": "mock"}).preflight()
    assert isinstance(report, pf.PreflightReport)
    assert not [c for c in report.checks if not c.ok and c.severity == pf.CRITICAL]


def test_preflight_fails_loud_without_tool_on_real_run() -> None:
    # Real run with no YCSB launcher on PATH/YCSB_HOME → CRITICAL with a hint.
    import shutil

    if shutil.which("ycsb"):  # environment actually has YCSB; skip the negative case
        return
    report = YcsbLocalAdapter({"mode": "runtime"}).preflight()
    crit = [c for c in report.checks if not c.ok and c.severity == pf.CRITICAL]
    assert crit and "ycsb" in crit[0].name.lower()
    assert "YCSB_HOME" in crit[0].remediation or "PATH" in crit[0].remediation
