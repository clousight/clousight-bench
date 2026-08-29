"""The official-mmlu-evaluator (pure function over RawArtifacts)."""

from __future__ import annotations

import json
from pathlib import Path

from clousight_bench.core.registry import load_evaluators
from clousight_bench.core.suite import RawArtifacts
from clousight_bench.suites.mmlu.evaluator import OfficialMmluEvaluator


def _artifacts(tmp_path: Path, answers: list[dict], summary: dict) -> RawArtifacts:
    (tmp_path / "answers.json").write_text(json.dumps(answers))
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    return RawArtifacts(
        dir=tmp_path,
        manifest={
            "answers": {"path": "answers.json", "rows": len(answers)},
            "summary": {"path": "summary.json", "rows": None},
        },
    )


def test_registered_via_entry_point() -> None:
    evs = {e.evaluator_id: e for e in load_evaluators()}
    assert "official-mmlu-evaluator" in evs
    assert isinstance(evs["official-mmlu-evaluator"], OfficialMmluEvaluator)


def test_supports_only_mmlu() -> None:
    ev = OfficialMmluEvaluator()
    assert ev.supports("mmlu", "llm-mock")
    assert not ev.supports("tpc-c", "llm-mock")


def test_accuracy_and_serving_dimensions(tmp_path: Path) -> None:
    answers = [
        {"id": "a", "predicted": 1, "gold": 1, "correct": True, "latency_ms": 100.0},
        {"id": "b", "predicted": 0, "gold": 2, "correct": False, "latency_ms": 200.0},
    ]
    summary = {"model": "m", "prompt_tokens": 900, "completion_tokens": 100}
    out = OfficialMmluEvaluator().evaluate(_artifacts(tmp_path, answers, summary))
    assert out["mmlu.accuracy"].value == 0.5
    assert out["mmlu.accuracy"].reproducibility_class == "deterministic"
    assert out["mmlu.accuracy"].official is True
    assert out["mmlu.avg_latency_ms"].value == 150.0
    assert out["mmlu.total_tokens"].value == 1000
    assert out["mmlu.cost_usd"].value > 0
    # every key is mmlu.-namespaced (conformance contract)
    assert all(k.startswith("mmlu.") for k in out)


def test_token_metrics_omitted_without_usage(tmp_path: Path) -> None:
    answers = [{"id": "a", "predicted": 1, "gold": 1, "correct": True, "latency_ms": 100.0}]
    out = OfficialMmluEvaluator().evaluate(_artifacts(tmp_path, answers, {"model": "m"}))
    assert "mmlu.accuracy" in out
    assert "mmlu.total_tokens" not in out and "mmlu.cost_usd" not in out


def test_missing_or_empty_answers(tmp_path: Path) -> None:
    assert OfficialMmluEvaluator().evaluate(_artifacts(tmp_path, [], {})) == {}


def test_evaluate_over_the_committed_mock_fixture() -> None:
    from clousight_bench.suites.mmlu.suite import MmluSuite

    out = OfficialMmluEvaluator().evaluate(MmluSuite().mock_artifacts({}))
    assert out["mmlu.accuracy"].value == 1.0  # mock fixture is all-correct
