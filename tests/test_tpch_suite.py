"""The tpc-h BenchmarkSuite (mock path + real duckdb-local run)."""

from __future__ import annotations

import importlib.util
import json

import pytest

from clousight_bench.core.registry import load_benchmark_suites
from clousight_bench.core.suite import DriverContext, RawArtifacts, Target
from clousight_bench.suites.tpc_h.suite import TpchSuite, result_digest

_DUCKDB = importlib.util.find_spec("duckdb") is not None


def test_suite_registered_via_entry_point() -> None:
    suites = load_benchmark_suites()
    assert "tpc-h" in suites
    assert isinstance(suites["tpc-h"], TpchSuite)


def test_module_imports_without_duckdb() -> None:
    import clousight_bench.suites.tpc_h.suite  # noqa: F401


def test_result_digest_is_order_independent_and_numeric_stable() -> None:
    a = result_digest([(1, "x", 3.14159), (2, "y", 2.0)])
    b = result_digest([(2, "y", 2.001), (1, "x", 3.14)])
    assert a == b
    assert a != result_digest([(1, "x", 3.14159), (2, "y", 9.99)])
    assert a.startswith("sha256:")


def test_resolve_digest_stable_and_sensitive() -> None:
    suite = TpchSuite()
    d1 = suite.resolve({"scale_factor": 1.0, "query_ids": [1, 6, 14]}, None)
    d2 = suite.resolve({"scale_factor": 1.0, "query_ids": [14, 1, 6]}, None)
    assert d1.digest == d2.digest
    assert d1.version == "duckdb-1.5.4/tpch/sf1-ref-v1/sf1"
    assert d1.payload["scale_factor"] == 1.0
    assert suite.resolve({"scale_factor": 10.0}, None).digest != d1.digest
    assert suite.resolve({"query_ids": [1]}, None).digest != d1.digest


def test_default_query_set_is_22() -> None:
    assert TpchSuite().resolve({}, None).payload["query_ids"] == list(range(1, 23))


def test_mock_artifacts_are_valid_and_offline() -> None:
    raw = TpchSuite().mock_artifacts({})
    assert isinstance(raw, RawArtifacts)
    q = json.loads(raw.path("queries").read_text())
    s = json.loads(raw.path("summary").read_text())
    assert {"queries", "summary"} <= set(raw.manifest)
    assert q and all(
        {"query_nr", "latency_ms", "row_count", "result_digest"} <= set(row) for row in q
    )
    assert s["scale_factor"] == 1.0
    assert s["query_count"] == len(q)


def test_run_delegates_to_mock_when_target_mock() -> None:
    suite = TpchSuite()
    env = suite.prepare(Target(mode="runtime", mock=True), suite.resolve({}, None), DriverContext("local"))
    assert env.payload.get("mock") is True
    raw = suite.run(Target(mode="runtime", mock=True), env, DriverContext("local"))
    assert {"queries", "summary"} <= set(raw.manifest)


@pytest.mark.slow
@pytest.mark.skipif(not _DUCKDB, reason="requires the [tpch] extra (duckdb)")
def test_real_run_digests_match_committed_reference() -> None:
    from pathlib import Path

    suite = TpchSuite()
    subset = [1, 6, 14]
    dataset = suite.resolve({"scale_factor": 1.0, "query_ids": subset}, None)
    env = suite.prepare(Target(mode="runtime", mock=False), dataset, DriverContext("local"))
    try:
        raw = suite.run(Target(mode="runtime", mock=False), env, DriverContext("local"))
        produced = {row["query_nr"]: row for row in json.loads(raw.path("queries").read_text())}
        reference = json.loads(
            (
                Path(__file__).resolve().parent.parent
                / "src/clousight_bench/suites/tpc_h/fixtures/reference/sf1_digests.json"
            ).read_text()
        )
        for nr in subset:
            assert produced[nr]["result_digest"] == reference[str(nr)]["result_digest"], nr
            assert produced[nr]["row_count"] == reference[str(nr)]["row_count"], nr
            assert produced[nr]["latency_ms"] > 0
    finally:
        suite.teardown(env)
