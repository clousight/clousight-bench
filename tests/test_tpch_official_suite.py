"""TPC-H official mode: suite run() + end-to-end through the orchestrator."""

from __future__ import annotations

import importlib.util
import json

import pytest

from clousight_bench.core import orchestrator as orch
from clousight_bench.core.schema import RunSpec
from clousight_bench.suites.tpc_h.suite import TpchSuite

_DUCKDB = importlib.util.find_spec("duckdb") is not None
_QPHH_EVAL = "official-tpch-qphh-evaluator"


def test_official_mock_artifacts_well_formed() -> None:
    raw = TpchSuite().mock_official_artifacts()
    doc = json.loads(raw.path("official").read_text())
    assert doc["scale_factor"] == 1.0
    assert len(doc["power"]["queries"]) == 22
    assert len(doc["throughput"]["query_streams"]) == 2
    assert doc["acid"]["durability"] == "n/a"


def test_official_mock_e2e_yields_qphh(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="data-warehouse",
        task_id="suite:tpc-h",
        platform="duckdb-local",
        target={"mode": "mock"},
        params={"mode": "official", "evaluator": _QPHH_EVAL},
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=False)
    assert record.status == "completed", f"got {record.status}: {record.errors}"
    assert record.provenance.evaluator_id == _QPHH_EVAL
    m = record.measurements
    assert m["tpc-h.qphh_at_size"]["value"] > 0
    assert m["tpc-h.qphh_at_size"]["unit"] == "QphH"
    assert m["tpc-h.qphh_at_size"]["reproducibility_class"] == "environmental"
    assert m["tpc-h.power_at_size"]["value"] > 0
    assert m["tpc-h.throughput_at_size"]["value"] > 0
    assert m["tpc-h.queries_passed"]["value"] == 1.0  # mock fixture is SF1
    assert m["tpc-h.acid_atomicity"]["value"] == 1.0


@pytest.mark.slow
@pytest.mark.skipif(not _DUCKDB, reason="requires the [tpch] extra (duckdb)")
def test_official_real_duckdb_small_sf(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="data-warehouse",
        task_id="suite:tpc-h",
        platform="duckdb-local",
        target={"mode": "runtime"},
        params={"mode": "official", "scale_factor": 0.01, "streams": 2, "evaluator": _QPHH_EVAL},
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=False)
    assert record.status == "completed", f"got {record.status}: {record.errors}"
    m = record.measurements
    assert m["tpc-h.qphh_at_size"]["value"] > 0
    assert m["tpc-h.power_at_size"]["value"] > 0
    assert m["tpc-h.throughput_at_size"]["value"] > 0
    assert m["tpc-h.load_time_s"]["value"] > 0
    # SF != 1 -> correctness intentionally omitted
    assert "tpc-h.queries_passed" not in m
    # ACID probes ran (best-effort) and produced pass/fail verdicts
    assert m["tpc-h.acid_atomicity"]["value"] in (0.0, 1.0)


@pytest.mark.slow
@pytest.mark.skipif(not _DUCKDB, reason="requires the [tpch] extra (duckdb)")
def test_official_real_duckdb_artifact_shape(tmp_path) -> None:
    from clousight_bench.core.suite import Target

    suite = TpchSuite()
    ds = suite.resolve({"mode": "official", "scale_factor": 0.01, "streams": 2}, None)
    env = suite.prepare(Target(mode="runtime", mock=False), ds, None)
    try:
        raw = suite.run(Target(mode="runtime", mock=False), env, None)
        doc = json.loads(raw.path("official").read_text())
        assert len(doc["power"]["queries"]) == 22
        assert len(doc["throughput"]["query_streams"]) == 2
        assert len(doc["throughput"]["refresh_stream"]) == 2
        assert doc["throughput"]["elapsed_s"] > 0
        assert doc["acid"]["durability"] == "n/a"
        assert doc["scale_factor"] == 0.01
    finally:
        suite.teardown(env)
