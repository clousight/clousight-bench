"""Judge live-wiring: params.judge builds a live judge for judge-based metrics,
and params.extra_metrics opts a metric into a run — end-to-end through
evaluate_with_metrics."""

from __future__ import annotations

from pathlib import Path

from clousight_bench.core.judge import JudgeModel
from clousight_bench.core.observation import ItemResult, Measurement
from clousight_bench.core.suite import Evaluator, RawArtifacts, evaluate_with_metrics


class _ScriptedJudge(JudgeModel):
    def __init__(self, verdict: str = "good") -> None:
        self._verdict = verdict

    def model_id(self) -> str:
        return "scripted"

    def generate(self, prompt: str) -> str:  # noqa: ARG002
        return f'{{"verdict": "{self._verdict}", "rationale": "ok"}}'


class _Eval(Evaluator):
    evaluator_id = "e"
    official = True
    extra_metric_ids = ()  # nothing bound by the suite

    def supports(self, suite_id, product):  # noqa: ARG002
        return True

    def evaluate(self, raw):  # noqa: ARG002
        return {"demo.accuracy": Measurement(value=1.0, unit="ratio")}

    def items(self, raw):  # noqa: ARG002
        return [ItemResult(item_id="q1", input="q", output="a")]


def _raw(tmp: Path) -> RawArtifacts:
    return RawArtifacts(dir=tmp, manifest={})


def test_response_quality_runs_with_a_configured_judge(tmp_path, monkeypatch) -> None:
    """params.extra_metrics opts response-quality in; params.judge builds the judge
    → a judge-based measurement is produced end-to-end."""
    import clousight_bench.core.registry as reg

    monkeypatch.setattr(reg, "build_judge", lambda cfg: _ScriptedJudge("good") if cfg else None)
    ms, items = evaluate_with_metrics(
        _Eval(),
        _raw(tmp_path),
        suite_id="demo",
        params={"extra_metrics": ["response_quality"], "judge": {"provider": "x"}},
    )
    assert ms["demo.response_quality"].value == 0.75  # good → 0.75
    assert ms["demo.response_quality"].reproducibility_class == "judge-based"
    assert any(s.metric == "response_quality" and s.status == "ok" for s in items[0].scores)


def test_response_quality_skips_without_judge_config(tmp_path) -> None:
    """No params.judge → judge is None → the metric skips (no measurement); safe
    for mock / CI runs that opt the metric in but supply no judge."""
    ms, items = evaluate_with_metrics(
        _Eval(), _raw(tmp_path), suite_id="demo", params={"extra_metrics": ["response_quality"]}
    )
    assert "demo.response_quality" not in ms  # all skipped → no aggregate
    assert any(s.metric == "response_quality" and s.status == "skip" for s in items[0].scores)


def test_extra_metrics_merges_with_bound_and_dedups(tmp_path) -> None:
    class _BoundEval(_Eval):
        extra_metric_ids = ("answered_rate",)

    ms, _ = evaluate_with_metrics(
        _BoundEval(),
        _raw(tmp_path),
        suite_id="demo",
        params={"extra_metrics": ["answered_rate"]},  # duplicate of bound → dedup
    )
    assert ms["demo.answered_rate"].value == 1.0  # one score, not doubled
