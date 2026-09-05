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
def test_official_generated_ordering_scales_past_two_streams(tmp_path) -> None:
    # The official Appendix A fixture only ships streams 0-2; the generated ordering
    # lets the Throughput test run more streams (here S=3, the SF10 official minimum).
    from clousight_bench.core.suite import Target

    suite = TpchSuite()
    ds = suite.resolve(
        {"mode": "official", "scale_factor": 0.01, "streams": 3, "query_order": "generated"}, None
    )
    env = suite.prepare(Target(mode="runtime", mock=False), ds, None)
    try:
        raw = suite.run(Target(mode="runtime", mock=False), env, None)
        doc = json.loads(raw.path("official").read_text())
        assert len(doc["throughput"]["query_streams"]) == 3
        assert len(doc["throughput"]["refresh_stream"]) == 3
        assert doc["ordering_source"].startswith("clousight-generated/")
    finally:
        suite.teardown(env)


@pytest.mark.skipif(not _DUCKDB, reason="requires the [tpch] extra (duckdb)")
def test_official_mode_streams_over_table_raises_without_generated() -> None:
    # official ordering only has streams 0-2 -> asking for S=3 fails loudly (no
    # silent fallback), directing the user to `query_order: generated` or Appendix A.
    from clousight_bench.core.suite import EnvHandle, Target

    fake = EnvHandle(
        {
            "mock": False,
            "mode": "official",
            "db_path": "/nonexistent.duckdb",
            "scale_factor": 10.0,
            "streams": 3,
            "query_ids": list(range(1, 23)),
            "query_order": "official",
            "load_time_s": 1.0,
        }
    )
    with pytest.raises(ValueError, match="query_order.json"):
        TpchSuite().run(Target(mode="runtime", mock=False), fake, None)


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
        # the official run reconstructs its trace: a v3 trajectory artifact
        from clousight_bench.core.sut_span import validate_span

        assert "trajectory" in raw.manifest
        for line in raw.path("trajectory").read_text().splitlines():
            if line.strip():
                validate_span(json.loads(line))
    finally:
        suite.teardown(env)


def test_official_mock_ships_a_v3_trajectory() -> None:
    from clousight_bench.core.sut_span import validate_span

    raw = TpchSuite().mock_official_artifacts()
    assert "trajectory" in raw.manifest
    lines = raw.path("trajectory").read_text().splitlines()
    spans = [json.loads(line) for line in lines if line.strip()]
    for span in spans:
        validate_span(span)
    names = {s["name"] for s in spans}
    assert "tpc-h.official" in names and "tpc-h.power" in names
    assert any(n.startswith("tpc-h.stream") for n in names)
