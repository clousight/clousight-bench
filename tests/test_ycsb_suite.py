"""The ycsb BenchmarkSuite (mock path + resolve)."""

from __future__ import annotations

import json

import pytest

from clousight_bench.core.registry import load_benchmark_suites
from clousight_bench.core.suite import DriverContext, RawArtifacts, Target
from clousight_bench.suites.ycsb.suite import YcsbSuite


def test_suite_registered_via_entry_point() -> None:
    suites = load_benchmark_suites()
    assert "ycsb" in suites
    assert isinstance(suites["ycsb"], YcsbSuite)


def test_resolve_digest_stable_and_sensitive() -> None:
    suite = YcsbSuite()
    d1 = suite.resolve({"workload": "workloada", "recordcount": 10000, "operationcount": 10000}, None)
    d2 = suite.resolve({"workload": "workloada", "recordcount": 10000, "operationcount": 10000}, None)
    assert d1.digest == d2.digest
    assert d1.version == "ycsb-0.17.0/workloada"
    assert d1.payload["workload"] == "workloada"
    assert suite.resolve({"workload": "workloadb"}, None).digest != d1.digest
    assert suite.resolve({"recordcount": 99}, None).digest != d1.digest


def test_resolve_rejects_unknown_workload() -> None:
    with pytest.raises(ValueError, match="unknown YCSB workload"):
        YcsbSuite().resolve({"workload": "workloadz"}, None)


def test_mock_artifacts_are_valid_and_offline() -> None:
    raw = YcsbSuite().mock_artifacts({})
    assert isinstance(raw, RawArtifacts)
    assert {"ycsb_output", "summary"} <= set(raw.manifest)
    text = raw.path("ycsb_output").read_text()
    assert "[OVERALL], Throughput(ops/sec)" in text
    summary = json.loads(raw.path("summary").read_text())
    assert summary["workload"] == "workloada"


def test_run_delegates_to_mock_when_target_mock() -> None:
    suite = YcsbSuite()
    env = suite.prepare(Target(mode="runtime", mock=True), suite.resolve({}, None), DriverContext("local"))
    assert env.payload.get("mock") is True  # never resolved a YCSB binary
    raw = suite.run(Target(mode="runtime", mock=True), env, DriverContext("local"))
    assert {"ycsb_output", "summary"} <= set(raw.manifest)


def test_binding_and_props_config_connect_from_endpoint() -> None:
    # The config-connect seam: an endpoint on the Target must surface as YCSB
    # redis.host/redis.port props (regression for the endpoint-not-threaded bug).
    from clousight_bench.domains.key_value.adapters.ycsb import YcsbEndpointAdapter
    from clousight_bench.suites.ycsb.suite import _binding_and_props

    adapter = YcsbEndpointAdapter({"binding": "redis", "endpoint": "db.internal:6380"})
    target = Target(mode="endpoint", mock=False, handle=adapter, endpoint="db.internal:6380")
    binding, props = _binding_and_props(target)
    assert binding == "redis"
    assert "-p" in props and "redis.host=db.internal" in props and "redis.port=6380" in props


def test_prepare_real_without_tool_fails_loud() -> None:
    import shutil

    if shutil.which("ycsb"):  # environment has YCSB — skip the negative case
        return
    suite = YcsbSuite()
    with pytest.raises(RuntimeError, match="YCSB"):
        suite.prepare(Target(mode="runtime", mock=False), suite.resolve({}, None), DriverContext("local"))
