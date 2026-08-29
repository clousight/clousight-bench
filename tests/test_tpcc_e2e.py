"""End-to-end: tpc-c suite through the orchestrator on transactional-db/benchbase-local."""

from __future__ import annotations

from clousight_bench.core import orchestrator as orch
from clousight_bench.core.schema import RunSpec


def test_mock_e2e_completes_with_tpcc_measurements(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="transactional-db",
        task_id="suite:tpc-c",
        platform="benchbase-local",
        target={"mode": "mock"},
        params={},
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=True)
    assert record.status == "completed", f"got {record.status}: {record.errors}"
    assert record.provenance.suite_id == "tpc-c"
    assert record.provenance.evaluator_id == "official-tpcc-evaluator"
    assert record.measurements["tpc-c.throughput_req_per_sec"]["value"] == 753.85
    assert "tpc-c.p99_latency_us" in record.measurements
    assert "tpc-c.goodput_req_per_sec" in record.measurements
