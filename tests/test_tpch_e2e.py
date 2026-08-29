"""End-to-end: tpc-h suite through the orchestrator on data-warehouse/duckdb-local."""

from __future__ import annotations

import importlib.util

import pytest

from clousight_bench.core import orchestrator as orch
from clousight_bench.core.schema import RunSpec

_DUCKDB = importlib.util.find_spec("duckdb") is not None


def test_mock_e2e_completes_with_tpch_measurements(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="data-warehouse",
        task_id="suite:tpc-h",
        platform="duckdb-local",
        target={"mode": "mock"},
        params={},
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=False)
    assert record.status == "completed", f"got {record.status}: {record.errors}"
    assert record.provenance.suite_id == "tpc-h"
    assert record.provenance.evaluator_id == "official-tpch-evaluator"
    assert record.measurements["tpc-h.queries_passed"]["value"] == 1.0
    assert "tpc-h.geomean_latency_ms" in record.measurements
    assert "tpc-h.total_runtime_ms" in record.measurements


@pytest.mark.slow
@pytest.mark.skipif(not _DUCKDB, reason="requires the [tpch] extra (duckdb)")
def test_real_duckdb_local_e2e_small_subset(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="data-warehouse",
        task_id="suite:tpc-h",
        platform="duckdb-local",
        target={"mode": "runtime"},
        params={"scale_factor": 1, "query_ids": [1, 6, 14]},
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=False)
    assert record.status == "completed", f"got {record.status}: {record.errors}"
    assert record.measurements["tpc-h.queries_passed"]["value"] == 1.0
    assert record.measurements["tpc-h.total_runtime_ms"]["value"] > 0
    assert record.provenance.suite_id == "tpc-h"
