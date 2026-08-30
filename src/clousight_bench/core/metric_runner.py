"""Run a set of :class:`Metric`s over a list of items with per-metric isolation.

This is the composition seam: apply N metrics to the same items, append their
per-item :class:`ItemScore`s, and collect their aggregated Measurements under a
namespace. Isolation is the R2 honesty guarantee — a metric that raises on an
item yields ``status="error"`` for that item and keeps going; a metric missing a
required input on an item yields ``status="skip"``; neither ever voids the run or
another metric.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from clousight_bench.core.metric import Metric, MetricContext
from clousight_bench.core.observation import ItemResult, ItemScore, Measurement


@dataclass
class MetricRunConfig:
    """Runner knobs, grouped so the signature stays stable as options grow."""

    fail_closed: bool = False  # if True, a metric erroring on ANY item re-raises
    # (async concurrency knobs land here with R4's judge metrics)


def _apply_one(metric: Metric, item: ItemResult, ctx: MetricContext, cfg: MetricRunConfig) -> None:
    """Score ``item`` with ``metric`` and append the result (isolated)."""
    missing = metric.missing_inputs(item)
    if missing:
        item.scores.append(
            ItemScore(
                metric=metric.metric_id,
                value=0.0,
                status="skip",
                reason=f"missing required input(s): {', '.join(missing)}",
            )
        )
        return
    try:
        score = metric.score_item(item, ctx)
    except Exception as exc:  # noqa: BLE001 - a metric bug is isolated, not fatal
        if cfg.fail_closed:
            raise
        item.scores.append(
            ItemScore(metric=metric.metric_id, value=0.0, status="error", error=str(exc)[-500:])
        )
        return
    if score is not None:
        item.scores.append(score)


def run_metrics(
    items: Sequence[ItemResult],
    metrics: Sequence[Metric],
    *,
    namespace: str,
    ctx: MetricContext | None = None,
    config: MetricRunConfig | None = None,
) -> tuple[list[ItemResult], dict[str, Measurement]]:
    """Apply ``metrics`` to ``items`` in place-ish; return ``(items, measurements)``.

    Each metric appends one :class:`ItemScore` per item (ok/fail/skip/error) and
    contributes ``{namespace}.{key}`` Measurements from its ``aggregate``. Metric
    ids are applied in order; the returned items are the same objects with scores
    appended.
    """
    ctx = ctx or MetricContext()
    cfg = config or MetricRunConfig()
    item_list = list(items)
    measurements: dict[str, Measurement] = {}
    for metric in metrics:
        for item in item_list:
            _apply_one(metric, item, ctx, cfg)
        for key, m in metric.aggregate(item_list).items():
            measurements[f"{namespace}.{key}"] = m
    return item_list, measurements
