"""End-to-end: mmlu suite through the orchestrator on llm/llm-mock."""

from __future__ import annotations

from clousight_bench.core import orchestrator as orch
from clousight_bench.core.schema import RunSpec


def test_mock_e2e_completes_with_mmlu_measurements(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="llm",
        task_id="suite:mmlu",
        platform="llm-mock",
        target={"mode": "mock"},
        params={},
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=True)
    assert record.status == "completed", f"got {record.status}: {record.errors}"
    assert record.provenance.suite_id == "mmlu"
    assert record.provenance.evaluator_id == "official-mmlu-evaluator"
    assert record.measurements["mmlu.accuracy"]["value"] == 1.0
    assert "mmlu.total_tokens" in record.measurements
