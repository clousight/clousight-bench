"""The official-gsm8k-evaluator."""

from __future__ import annotations

import json
from pathlib import Path

from clousight_bench.core.registry import load_evaluators
from clousight_bench.core.suite import RawArtifacts
from clousight_bench.suites.gsm8k.evaluator import OfficialGsm8kEvaluator


def _art(tmp, answers, summary):
    (tmp / "answers.json").write_text(json.dumps(answers))
    (tmp / "summary.json").write_text(json.dumps(summary))
    return RawArtifacts(
        dir=tmp, manifest={"answers": {"path": "answers.json"}, "summary": {"path": "summary.json"}}
    )


def test_registered_and_supports() -> None:
    ev = {e.evaluator_id: e for e in load_evaluators()}["official-gsm8k-evaluator"]
    assert ev.supports("gsm8k", "llm-mock") and not ev.supports("mmlu", "llm-mock")


def test_accuracy_and_dims(tmp_path: Path) -> None:
    answers = [
        {"id": "a", "predicted": "5", "gold": "5", "correct": True, "latency_ms": 100.0},
        {"id": "b", "predicted": "9", "gold": "8", "correct": False, "latency_ms": 300.0},
    ]
    out = OfficialGsm8kEvaluator().evaluate(
        _art(tmp_path, answers, {"prompt_tokens": 800, "completion_tokens": 200})
    )
    assert out["gsm8k.accuracy"].value == 0.5
    assert out["gsm8k.accuracy"].reproducibility_class == "deterministic"
    assert out["gsm8k.avg_latency_ms"].value == 200.0
    assert out["gsm8k.total_tokens"].value == 1000
    assert all(k.startswith("gsm8k.") for k in out)


def test_empty_returns_empty(tmp_path: Path) -> None:
    assert OfficialGsm8kEvaluator().evaluate(_art(tmp_path, [], {})) == {}


def test_over_mock_fixture() -> None:
    from clousight_bench.suites.gsm8k.suite import Gsm8kSuite

    out = OfficialGsm8kEvaluator().evaluate(Gsm8kSuite().mock_artifacts({}))
    assert out["gsm8k.accuracy"].value == 1.0
