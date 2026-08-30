"""Direct tests for core.metric_runner (was only covered indirectly via test_metric)."""

from __future__ import annotations

import pytest

from clousight_bench.core.metric import Metric, MetricContext
from clousight_bench.core.metric_runner import MetricRunConfig, run_metrics
from clousight_bench.core.observation import ItemResult, ItemScore


class _Const(Metric):
    metric_id = "c"

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


class _None(Metric):
    metric_id = "noscore"

    def score_item(self, item, ctx):  # noqa: ARG002
        return None  # opt out of scoring this item


def _items(n: int = 2) -> list[ItemResult]:
    return [ItemResult(item_id=f"i{k}", output=k) for k in range(n)]


def test_appends_one_score_per_metric_per_item_and_namespaces() -> None:
    items, ms = run_metrics(_items(2), [_Const()], namespace="ns")
    assert all(len(it.scores) == 1 and it.scores[0].metric == "c" for it in items)
    assert set(ms) == {"ns.c"} and ms["ns.c"].value == 1.0


def test_missing_required_input_is_skip_not_error() -> None:
    items = [ItemResult(item_id="a", output=1), ItemResult(item_id="b")]  # b has no output
    out, ms = run_metrics(items, [_NeedsOutput()], namespace="ns")
    assert [it.scores[0].status for it in out] == ["ok", "skip"]
    assert out[1].scores[0].reason.startswith("missing required input")
    assert ms["ns.needs_out"].value == 1.0  # skip excluded from denominator


def test_crashing_metric_isolated_as_error_run_continues() -> None:
    items, ms = run_metrics(_items(1), [_Boom(), _Const()], namespace="ns")
    boom = [s for s in items[0].scores if s.metric == "boom"][0]
    assert boom.status == "error" and "kaboom" in boom.error
    assert ms["ns.c"].value == 1.0  # the other metric still ran + aggregated
    assert "ns.boom" not in ms  # all-error → no aggregate


def test_fail_closed_reraises() -> None:
    with pytest.raises(RuntimeError, match="kaboom"):
        run_metrics(_items(1), [_Boom()], namespace="ns", config=MetricRunConfig(fail_closed=True))


def test_none_score_leaves_item_unscored_by_that_metric() -> None:
    items, ms = run_metrics(_items(2), [_None()], namespace="ns")
    assert all(it.scores == [] for it in items)  # metric opted out → no score appended
    assert ms == {}  # nothing scored → no aggregate


def test_ctx_params_and_judge_reach_the_metric() -> None:
    seen: dict = {}

    class _Peek(Metric):
        metric_id = "peek"

        def score_item(self, item, ctx):  # noqa: ARG002
            seen["params"] = ctx.params
            seen["judge"] = ctx.judge
            return ItemScore(metric=self.metric_id, value=1.0, status="ok")

    run_metrics(_items(1), [_Peek()], namespace="ns", ctx=MetricContext(params={"k": "v"}, judge="J"))
    assert seen == {"params": {"k": "v"}, "judge": "J"}
