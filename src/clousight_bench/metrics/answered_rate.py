"""Answered-rate metric — the fraction of items the SUT actually answered.

A deterministic, judge-free add-on metric that demonstrates the plugin point:
an item counts as answered when it has a non-null ``output`` (for the llm suites,
``output`` is the parsed prediction — ``None`` means the model produced nothing
parseable, i.e. a refusal or a format miss). Complements a suite's objective
accuracy: a model can be accurate on what it answers yet answer little.
"""

from __future__ import annotations

from clousight_bench.core.metric import Metric, MetricContext
from clousight_bench.core.observation import ItemResult, ItemScore


class AnsweredRateMetric(Metric):
    metric_id = "answered_rate"
    reproducibility_class = "deterministic"
    unit = "ratio"
    default_how = "ratio"

    def score_item(self, item: ItemResult, ctx: MetricContext) -> ItemScore:  # noqa: ARG002
        answered = item.output is not None
        return ItemScore(
            metric=self.metric_id,
            value=1.0 if answered else 0.0,
            status="ok" if answered else "fail",
        )
