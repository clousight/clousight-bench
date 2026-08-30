"""R2: composable Metric plugin point — ABC, runner isolation, registry, binding."""

from __future__ import annotations

from pathlib import Path

import pytest

from clousight_bench.core.metric import Metric
from clousight_bench.core.metric_runner import MetricRunConfig, run_metrics
from clousight_bench.core.observation import ItemResult, ItemScore
from clousight_bench.core.registry import RegistryError, load_metrics
from clousight_bench.core.suite import RawArtifacts, evaluate_with_metrics
from clousight_bench.metrics.answered_rate import AnsweredRateMetric


def _item(item_id: str, **kw) -> ItemResult:
    return ItemResult(item_id=item_id, **kw)


# --- a couple of test-local metrics to exercise the contract ------------------


class _ConstMetric(Metric):
    metric_id = "constt"
    reproducibility_class = "deterministic"

    def score_item(self, item, ctx):  # noqa: ARG002
        return ItemScore(metric=self.metric_id, value=1.0, status="ok")


class _NeedsOutput(Metric):
    metric_id = "needs_out"
    required_inputs = ("output",)

    def score_item(self, item, ctx):  # noqa: ARG002
        return ItemScore(metric=self.metric_id, value=1.0, status="ok")


class _Boom(Metric):
    metric_id = "boom"

    def score_item(self, item, ctx):  # noqa: ARG002
        raise RuntimeError("kaboom")


# --- runner -------------------------------------------------------------------


def test_run_metrics_composes_multiple_and_namespaces() -> None:
    items = [_item("a", output=1), _item("b", output=2)]
    out_items, ms = run_metrics(items, [_ConstMetric(), AnsweredRateMetric()], namespace="suite")
    # each item now carries one score per metric
    assert {s.metric for s in out_items[0].scores} == {"constt", "answered_rate"}
    assert set(ms) == {"suite.constt", "suite.answered_rate"}
    assert ms["suite.answered_rate"].value == 1.0


def test_run_metrics_isolates_a_crashing_metric() -> None:
    items = [_item("a", output=1)]
    out_items, ms = run_metrics(items, [_Boom(), _ConstMetric()], namespace="s")
    boom = [s for s in out_items[0].scores if s.metric == "boom"][0]
    assert boom.status == "error" and "kaboom" in boom.error
    # the OTHER metric still ran + aggregated
    assert ms["s.constt"].value == 1.0
    assert "s.boom" not in ms  # all-error → nothing scored → no aggregate


def test_run_metrics_fail_closed_reraises() -> None:
    with pytest.raises(RuntimeError, match="kaboom"):
        run_metrics([_item("a")], [_Boom()], namespace="s", config=MetricRunConfig(fail_closed=True))


def test_run_metrics_skips_on_missing_required_input() -> None:
    items = [_item("a", output=1), _item("b")]  # b has no output
    out_items, ms = run_metrics(items, [_NeedsOutput()], namespace="s")
    statuses = {it.item_id: it.scores[0].status for it in out_items}
    assert statuses == {"a": "ok", "b": "skip"}
    assert ms["s.needs_out"].value == 1.0  # only the scored (ok) item counts


# --- answered_rate ------------------------------------------------------------


def test_answered_rate_scores_null_output_as_unanswered() -> None:
    items = [_item("a", output=3), _item("b", output=None), _item("c", output=0)]
    _, ms = run_metrics(items, [AnsweredRateMetric()], namespace="mmlu")
    assert ms["mmlu.answered_rate"].value == pytest.approx(2 / 3)  # a,c answered; b not


# --- registry -----------------------------------------------------------------


def test_answered_rate_registered_via_entry_point() -> None:
    metrics = load_metrics()
    assert "answered_rate" in metrics
    assert isinstance(metrics["answered_rate"], AnsweredRateMetric)


def test_load_metrics_only_filter_and_unknown_fails_loud() -> None:
    assert set(load_metrics(only=("answered_rate",))) == {"answered_rate"}
    with pytest.raises(RegistryError, match="unknown metric"):
        load_metrics(only=("nope",))


# --- evaluate_with_metrics binding -------------------------------------------


class _FakeEval:
    evaluator_id = "fake"
    official = True
    extra_metric_ids = ("answered_rate",)

    def evaluate(self, raw):  # noqa: ARG002
        from clousight_bench.core.observation import Measurement

        return {"demo.accuracy": Measurement(value=1.0, unit="ratio")}

    def items(self, raw):  # noqa: ARG002
        return [_item("a", output=1), _item("b", output=None)]


def test_evaluate_with_metrics_merges_bound_metric(tmp_path: Path) -> None:
    raw = RawArtifacts(dir=tmp_path, manifest={})
    ms, items = evaluate_with_metrics(_FakeEval(), raw, suite_id="demo")
    assert ms["demo.accuracy"].value == 1.0
    assert ms["demo.answered_rate"].value == 0.5  # merged, namespaced under suite
    # the metric's per-item scores were appended
    assert any(s.metric == "answered_rate" for s in items[0].scores)


def test_evaluate_with_metrics_no_items_skips_addons(tmp_path: Path) -> None:
    class _NoItems(_FakeEval):
        def items(self, raw):  # noqa: ARG002
            return []

    ms, items = evaluate_with_metrics(_NoItems(), RawArtifacts(dir=tmp_path, manifest={}), suite_id="demo")
    assert "demo.answered_rate" not in ms  # no items → nothing to score
    assert items == []
