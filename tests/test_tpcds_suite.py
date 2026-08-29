"""The tpc-ds BenchmarkSuite (mock path + real duckdb-local run)."""

from __future__ import annotations

import importlib.util
import json

import pytest

from clousight_bench.core.registry import load_benchmark_suites
from clousight_bench.core.suite import DriverContext, RawArtifacts, Target
from clousight_bench.suites.tpc_ds.suite import TpcdsSuite, result_digest

_DUCKDB = importlib.util.find_spec("duckdb") is not None


def test_suite_registered_via_entry_point() -> None:
    suites = load_benchmark_suites()
    assert "tpc-ds" in suites
    assert isinstance(suites["tpc-ds"], TpcdsSuite)


def test_module_imports_without_duckdb() -> None:
    # suite.py must import cleanly without the [tpcds] extra (duckdb is lazy).
    import clousight_bench.suites.tpc_ds.suite  # noqa: F401


def test_result_digest_is_order_independent_and_numeric_stable() -> None:
    a = result_digest([(1, "x", 3.14159), (2, "y", 2.0)])
    b = result_digest([(2, "y", 2.001), (1, "x", 3.14)])  # reordered + rounds equal
    assert a == b  # sorted rows + 2dp rounding → identical digest
    c = result_digest([(1, "x", 3.14159), (2, "y", 9.99)])
    assert a != c
    assert a.startswith("sha256:")


def test_resolve_digest_stable_and_sensitive() -> None:
    suite = TpcdsSuite()
    d1 = suite.resolve({"scale_factor": 1.0, "query_ids": [3, 7, 42]}, None)
    d2 = suite.resolve({"scale_factor": 1.0, "query_ids": [42, 3, 7]}, None)  # order-independent
    assert d1.digest == d2.digest
    assert d1.version == "duckdb-1.5.4/tpcds/sf1-ref-v1/sf1"
    assert d1.payload["scale_factor"] == 1.0
    # sf change moves the digest
    assert suite.resolve({"scale_factor": 10.0}, None).digest != d1.digest
    # query set change moves the digest
    assert suite.resolve({"query_ids": [3]}, None).digest != d1.digest


def test_mock_artifacts_are_valid_and_offline() -> None:
    suite = TpcdsSuite()
    raw = suite.mock_artifacts({})
    assert isinstance(raw, RawArtifacts)
    # manifest resolves both named files
    q = json.loads(raw.path("queries").read_text())
    s = json.loads(raw.path("summary").read_text())
    assert {"queries", "summary"} <= set(raw.manifest)
    # queries.json schema shape
    assert q and all({"query_nr", "latency_ms", "row_count", "result_digest"} <= set(row) for row in q)
    assert s["scale_factor"] == 1.0
    assert s["query_count"] == len(q)


def test_run_delegates_to_mock_when_target_mock() -> None:
    suite = TpcdsSuite()
    env = suite.prepare(Target(mode="runtime", mock=True), suite.resolve({}, None), DriverContext("local"))
    assert env.payload.get("mock") is True  # never touched duckdb
    raw = suite.run(Target(mode="runtime", mock=True), env, DriverContext("local"))
    assert {"queries", "summary"} <= set(raw.manifest)


@pytest.mark.slow
@pytest.mark.skipif(not _DUCKDB, reason="requires the [tpcds] extra (duckdb)")
def test_real_run_digests_match_committed_reference() -> None:
    # The cross-platform stability gate: a real SF1 run of a small subset must
    # reproduce the committed reference digests exactly (same normalization).
    from pathlib import Path

    suite = TpcdsSuite()
    subset = [3, 7, 42]
    dataset = suite.resolve({"scale_factor": 1.0, "query_ids": subset}, None)
    env = suite.prepare(Target(mode="runtime", mock=False), dataset, DriverContext("local"))
    try:
        raw = suite.run(Target(mode="runtime", mock=False), env, DriverContext("local"))
        produced = {row["query_nr"]: row for row in json.loads(raw.path("queries").read_text())}
        reference = json.loads(
            (
                Path(__file__).resolve().parent.parent
                / "src/clousight_bench/suites/tpc_ds/fixtures/reference/sf1_digests.json"
            ).read_text()
        )
        for nr in subset:
            assert produced[nr]["result_digest"] == reference[str(nr)]["result_digest"], nr
            assert produced[nr]["row_count"] == reference[str(nr)]["row_count"], nr
            assert produced[nr]["latency_ms"] > 0
    finally:
        suite.teardown(env)
