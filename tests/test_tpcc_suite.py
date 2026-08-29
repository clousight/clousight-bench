"""The tpc-c BenchmarkSuite (mock path + resolve + config-connect seam)."""

from __future__ import annotations

import json

import pytest

from clousight_bench.core.registry import load_benchmark_suites
from clousight_bench.core.suite import DriverContext, RawArtifacts, Target
from clousight_bench.suites.tpc_c.suite import TpccSuite, _dbtype_and_endpoint


def test_suite_registered_via_entry_point() -> None:
    suites = load_benchmark_suites()
    assert "tpc-c" in suites
    assert isinstance(suites["tpc-c"], TpccSuite)


def test_resolve_digest_stable_and_sensitive() -> None:
    suite = TpccSuite()
    d1 = suite.resolve({"scalefactor": 1, "terminals": 1, "time": 60}, None)
    d2 = suite.resolve({"scalefactor": 1, "terminals": 1, "time": 60}, None)
    assert d1.digest == d2.digest
    assert d1.version == "benchbase-2023/sf1"
    assert d1.payload["scalefactor"] == 1
    assert suite.resolve({"scalefactor": 10}, None).digest != d1.digest
    assert suite.resolve({"terminals": 8}, None).digest != d1.digest


def test_mock_artifacts_are_valid_and_offline() -> None:
    raw = TpccSuite().mock_artifacts({})
    assert isinstance(raw, RawArtifacts)
    assert {"summary", "meta"} <= set(raw.manifest)
    summary = json.loads(raw.path("summary").read_text())
    assert "Throughput (requests/second)" in summary
    assert "Latency Distribution" in summary


def test_run_delegates_to_mock_when_target_mock() -> None:
    suite = TpccSuite()
    env = suite.prepare(Target(mode="runtime", mock=True), suite.resolve({}, None), DriverContext("local"))
    assert env.payload.get("mock") is True
    raw = suite.run(Target(mode="runtime", mock=True), env, DriverContext("local"))
    assert {"summary", "meta"} <= set(raw.manifest)


def test_dbtype_and_endpoint_config_connect_seam() -> None:
    # The config-connect seam: dbtype from the adapter, endpoint from the Target.
    from clousight_bench.domains.transactional_db.adapters.benchbase import JdbcEndpointAdapter

    adapter = JdbcEndpointAdapter({"dbtype": "postgres", "endpoint": "db.internal:5432"})
    target = Target(mode="endpoint", mock=False, handle=adapter, endpoint="db.internal:5432")
    dbtype, endpoint = _dbtype_and_endpoint(target)
    assert dbtype == "postgres"
    assert endpoint == "db.internal:5432"


def test_prepare_real_without_tool_fails_loud() -> None:
    import shutil

    if shutil.which("benchbase"):
        return
    suite = TpccSuite()
    with pytest.raises(RuntimeError, match="BenchBase"):
        suite.prepare(Target(mode="runtime", mock=False), suite.resolve({}, None), DriverContext("local"))
