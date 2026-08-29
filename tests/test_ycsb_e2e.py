"""End-to-end: ycsb suite through the orchestrator on key-value/ycsb-local."""

from __future__ import annotations

from clousight_bench.core import orchestrator as orch
from clousight_bench.core.schema import RunSpec


def test_mock_e2e_completes_with_ycsb_measurements(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="key-value",
        task_id="suite:ycsb",
        platform="ycsb-local",
        target={"mode": "mock"},
        params={},
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=True)
    assert record.status == "completed", f"got {record.status}: {record.errors}"
    assert record.provenance.suite_id == "ycsb"
    assert record.provenance.evaluator_id == "official-ycsb-evaluator"
    assert record.measurements["ycsb.throughput_ops"]["value"] == 7027.4
    assert "ycsb.read_p99_us" in record.measurements
    assert "ycsb.update_p99_us" in record.measurements
