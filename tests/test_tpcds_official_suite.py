"""TPC-DS official mode: QphDS evaluator + suite mock/real paths."""

from __future__ import annotations

import importlib.util
import json

import pytest

from clousight_bench.core import orchestrator as orch
from clousight_bench.core.registry import load_evaluators
from clousight_bench.core.schema import RunSpec
from clousight_bench.core.sut_span import validate_span
from clousight_bench.suites.tpc_ds.official_evaluator import OfficialTpcdsQphdsEvaluator
from clousight_bench.suites.tpc_ds.suite import TpcdsSuite

_DUCKDB = importlib.util.find_spec("duckdb") is not None
_EVAL = "official-tpcds-qphds-evaluator"


def test_registered_via_entry_point() -> None:
    evs = {e.evaluator_id: e for e in load_evaluators()}
    assert _EVAL in evs
    assert isinstance(evs[_EVAL], OfficialTpcdsQphdsEvaluator)


def test_official_mock_artifacts_well_formed() -> None:
    raw = TpcdsSuite().mock_official_artifacts()
    doc = json.loads(raw.path("official").read_text())
    assert doc["streams"] == 4  # DS official minimum
    assert len(doc["power"]["queries"]) == 99
    assert doc["ordering_source"].startswith("clousight-generated/")
    assert doc["acid"]["consistency"] == "n/a"
    # v3 trajectory ships with the mock so the waterfall renders offline
    for line in raw.path("trajectory").read_text().splitlines():
        if line.strip():
            validate_span(json.loads(line))


def test_official_mock_e2e_yields_qphds(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="data-warehouse",
        task_id="suite:tpc-ds",
        platform="duckdb-local",
        target={"mode": "mock"},
        params={"mode": "official", "evaluator": _EVAL},
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=False)
    assert record.status == "completed", f"got {record.status}: {record.errors}"
    m = record.measurements
    q = m["tpc-ds.qphds_at_size"]
    assert q["value"] > 0 and q["value"] == int(q["value"])  # spec floors the composite
    assert q["unit"] == "QphDS"
    assert q["reproducibility_class"] == "environmental"
    assert "unaudited" in q["notes"]
    assert m["tpc-ds.queries_passed"]["value"] == 1.0  # mock power stream carries SF1 digests
    assert m["tpc-ds.acid_atomicity"]["value"] == 1.0
    assert "tpc-ds.acid_consistency" not in m  # n/a → omitted, never invented
    assert m["tpc-ds.power_test_s"]["value"] > 0
    assert m["tpc-ds.maintenance_test_s"]["value"] > 0


def test_evaluator_never_raises_on_broken_artifact(tmp_path) -> None:
    from clousight_bench.core.suite import RawArtifacts

    (tmp_path / "official.json").write_text("{not json")
    raw = RawArtifacts(dir=tmp_path, manifest={"official": {"path": "official.json", "rows": None}})
    assert OfficialTpcdsQphdsEvaluator().evaluate(raw) == {}


@pytest.mark.slow
@pytest.mark.skipif(not _DUCKDB, reason="requires the [tpcds] extra (duckdb)")
def test_official_real_duckdb_small_sf(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="data-warehouse",
        task_id="suite:tpc-ds",
        platform="duckdb-local",
        target={"mode": "runtime"},
        params={
            "mode": "official",
            "scale_factor": 0.5,
            "streams": 2,
            "query_ids": [1, 3, 7, 42],  # subset keeps the slow lane fast
            "evaluator": _EVAL,
        },
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=False)
    assert record.status == "completed", f"got {record.status}: {record.errors}"
    m = record.measurements
    assert m["tpc-ds.qphds_at_size"]["value"] > 0
    assert m["tpc-ds.power_test_s"]["value"] > 0
    assert m["tpc-ds.maintenance_test_s"]["value"] > 0
    assert m["tpc-ds.acid_atomicity"]["value"] in (0.0, 1.0)
    # SF != 1 → correctness omitted
    assert "tpc-ds.queries_passed" not in m
