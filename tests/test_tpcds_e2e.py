"""End-to-end: tpc-ds suite through the orchestrator on data-warehouse/duckdb-local."""

from __future__ import annotations

import importlib.util

import pytest

from clousight_bench.core import orchestrator as orch
from clousight_bench.core.schema import RunSpec

_DUCKDB = importlib.util.find_spec("duckdb") is not None


def test_mock_e2e_completes_with_tpcds_measurements(tmp_path, monkeypatch) -> None:
    """orchestrator.execute() runs the tpc-ds suite in mock mode → schema-0.3 record."""
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="data-warehouse",
        task_id="suite:tpc-ds",
        platform="duckdb-local",
        target={"mode": "mock"},
        params={},
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=False)
    assert record.status == "completed", f"got {record.status}: {record.errors}"
    assert record.provenance.suite_id == "tpc-ds"
    assert record.provenance.evaluator_id == "official-tpcds-evaluator"
    # mock fixture is SF1 with 3 queries all matching the reference
    assert record.measurements["tpc-ds.queries_passed"]["value"] == 1.0
    assert "tpc-ds.geomean_latency_ms" in record.measurements
    assert "tpc-ds.total_runtime_ms" in record.measurements


@pytest.mark.slow
@pytest.mark.skipif(not _DUCKDB, reason="requires the [tpcds] extra (duckdb)")
def test_real_duckdb_local_e2e_small_subset(tmp_path, monkeypatch) -> None:
    """A real duckdb-local SF1 run of a small subset → completed, queries_passed==1.0."""
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="data-warehouse",
        task_id="suite:tpc-ds",
        platform="duckdb-local",
        target={"mode": "runtime"},  # not mock → real duckdb path
        params={"scale_factor": 1, "query_ids": [3, 7, 42]},
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=False)
    assert record.status == "completed", f"got {record.status}: {record.errors}"
    assert record.measurements["tpc-ds.queries_passed"]["value"] == 1.0
    assert record.measurements["tpc-ds.total_runtime_ms"]["value"] > 0
    assert record.provenance.suite_id == "tpc-ds"
