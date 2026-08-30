"""R1b: the llm suites emit a per-item substrate that reconciles to their scalars."""

from __future__ import annotations

import json
from pathlib import Path

from clousight_bench.core.aggregate import scored_values
from clousight_bench.core.suite import RawArtifacts
from clousight_bench.suites.gsm8k.evaluator import OfficialGsm8kEvaluator
from clousight_bench.suites.human_eval.evaluator import OfficialHumanEvalEvaluator
from clousight_bench.suites.mmlu.evaluator import OfficialMmluEvaluator


def _raw(tmp: Path, rows_name: str, rows: list, summary: dict) -> RawArtifacts:
    (tmp / f"{rows_name}.json").write_text(json.dumps(rows))
    (tmp / "summary.json").write_text(json.dumps(summary))
    return RawArtifacts(
        dir=tmp,
        manifest={rows_name: {"path": f"{rows_name}.json"}, "summary": {"path": "summary.json"}},
    )


# --------------------------------------------------------------------------- mmlu


def test_mmlu_items_carry_subject_group_and_scores(tmp_path: Path) -> None:
    rows = [
        {"id": "q1", "subject": "algebra", "predicted": 0, "gold": 0, "correct": True, "latency_ms": 10.0},
        {"id": "q2", "subject": "algebra", "predicted": 1, "gold": 2, "correct": False, "latency_ms": 20.0},
        {"id": "q3", "subject": "history", "predicted": 3, "gold": 3, "correct": True, "latency_ms": 30.0},
    ]
    items = OfficialMmluEvaluator().items(_raw(tmp_path, "answers", rows, {}))
    assert [it.item_id for it in items] == ["q1", "q2", "q3"]
    assert items[0].group == "algebra"
    assert items[0].scores[0].metric == "accuracy"
    assert items[0].scores[0].status == "ok" and items[1].scores[0].status == "fail"
    assert items[0].usage["latency_ms"] == 10.0


def test_mmlu_accuracy_reconciles_and_breaks_down_by_subject(tmp_path: Path) -> None:
    rows = [
        {"id": "q1", "subject": "algebra", "correct": True, "latency_ms": 10.0},
        {"id": "q2", "subject": "algebra", "correct": False, "latency_ms": 20.0},
        {"id": "q3", "subject": "history", "correct": True, "latency_ms": 30.0},
    ]
    ev = OfficialMmluEvaluator()
    raw = _raw(tmp_path, "answers", rows, {"prompt_tokens": 100, "completion_tokens": 20})
    out = ev.evaluate(raw)
    items = ev.items(raw)
    # headline reconciles with the per-item mean
    assert out["mmlu.accuracy"].value == sum(scored_values(items, "accuracy")) / len(items)
    assert out["mmlu.accuracy"].value == 2 / 3
    assert out["mmlu.accuracy"].ci is not None
    # per-subject breakdown
    assert out["mmlu.accuracy.by_group.algebra"].value == 0.5
    assert out["mmlu.accuracy.by_group.history"].value == 1.0
    # serving dims still present + namespaced/official
    assert out["mmlu.avg_latency_ms"].value == 20.0
    assert all(k.startswith("mmlu.") for k in out)
    assert all(m.official is True for m in out.values())


# -------------------------------------------------------------------------- gsm8k


def test_gsm8k_items_and_reconciliation(tmp_path: Path) -> None:
    rows = [
        {"id": "p1", "predicted": "42", "gold": "42", "correct": True, "latency_ms": 5.0},
        {"id": "p2", "predicted": "0", "gold": "7", "correct": False, "latency_ms": 6.0},
    ]
    ev = OfficialGsm8kEvaluator()
    raw = _raw(tmp_path, "answers", rows, {})
    items = ev.items(raw)
    assert [it.item_id for it in items] == ["p1", "p2"]
    assert ev.evaluate(raw)["gsm8k.accuracy"].value == 0.5


# ---------------------------------------------------------------------- human-eval


def test_human_eval_items_and_reconciliation(tmp_path: Path) -> None:
    rows = [
        {"task_id": "HumanEval/0", "passed": True, "latency_ms": 12.0},
        {"task_id": "HumanEval/1", "passed": False, "latency_ms": 8.0},
        {"task_id": "HumanEval/2", "passed": True, "latency_ms": 9.0},
    ]
    ev = OfficialHumanEvalEvaluator()
    raw = _raw(tmp_path, "results", rows, {})
    items = ev.items(raw)
    assert [it.item_id for it in items] == ["HumanEval/0", "HumanEval/1", "HumanEval/2"]
    assert items[0].scores[0].metric == "pass_at_1"
    out = ev.evaluate(raw)
    assert out["human-eval.pass_at_1"].value == 2 / 3
    assert out["human-eval.pass_at_1"].ci is not None


def test_no_group_means_no_by_group_keys(tmp_path: Path) -> None:
    """human-eval items have no group → no by_group measurements (the set-assert
    in test_human_eval stays valid)."""
    rows = [{"task_id": "HumanEval/0", "passed": True, "latency_ms": 1.0}]
    out = OfficialHumanEvalEvaluator().evaluate(_raw(tmp_path, "results", rows, {}))
    assert not any("by_group" in k for k in out)


def test_migrated_evaluators_are_fail_safe(tmp_path: Path) -> None:
    missing = RawArtifacts(dir=tmp_path, manifest={"answers": {"path": "nope.json"}})
    assert OfficialMmluEvaluator().evaluate(missing) == {}
    assert OfficialMmluEvaluator().items(missing) == []
