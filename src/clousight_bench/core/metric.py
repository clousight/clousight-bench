"""The composable Metric plugin point (R2).

A :class:`Metric` scores one :class:`ItemResult` at a time and defines how its
per-item scores aggregate into Measurements. This decouples *metrics* from
*suites*: a run can apply several metrics over the same items (the suite's own
objective metric lives in its evaluator; add-on metrics — an answered-rate, a
cost-efficiency, later a judge rubric — plug in here without forking the
evaluator). Metrics do NOT call each other; composition is the runner applying N
of them (see :mod:`clousight_bench.core.metric_runner`).

Contract mirrors the rest of the eval core:
- ``score_item`` returns an :class:`ItemScore` (or ``None`` to not score an item)
  with a 4-state ``status``; a metric that raises is isolated per-item by the
  runner (``status="error"``), so one metric never voids a run.
- ``required_inputs`` declares which :class:`ItemResult` fields the metric needs;
  the runner skips items missing them (``status="skip"``) rather than crashing.
- ``aggregate`` turns the metric's per-item scores into namespaced Measurements.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from clousight_bench.core.aggregate import aggregate
from clousight_bench.core.observation import ItemResult, ItemScore, Measurement


@dataclass
class MetricContext:
    """What a metric needs beyond a single item: run params (and, from R4, a judge)."""

    params: dict[str, Any] = field(default_factory=dict)
    judge: Any = None  # a JudgeModel, wired in R4; None for deterministic metrics


class Metric(ABC):
    """A composable, pluggable scorer applied per item then aggregated."""

    metric_id: str = "abstract"
    reproducibility_class: str = "deterministic"
    unit: str = "ratio"
    default_how: str = "ratio"
    # ItemResult field names this metric reads (e.g. ("output",)); items missing
    # any of them are scored ``skip`` rather than crashing the metric.
    required_inputs: tuple[str, ...] = ()
    requires_plugin_api: str = ">=1.0,<2.0"

    @abstractmethod
    def score_item(self, item: ItemResult, ctx: MetricContext) -> ItemScore | None:
        """Score one item. Return ``None`` to leave the item unscored by this
        metric. Raise to signal a metric bug (the runner isolates it as
        ``status="error"``)."""

    def aggregate(self, items: list[ItemResult]) -> dict[str, Measurement]:
        """Aggregate this metric's per-item scores into ``{metric_id: Measurement}``.

        Default: ``core.aggregate`` over the metric's own scores using
        ``default_how``/``unit``/``reproducibility_class``. Returns ``{}`` when no
        item carries a scored value for this metric.
        """
        m = aggregate(
            items,
            self.metric_id,
            self.default_how,
            unit=self.unit,
            reproducibility_class=self.reproducibility_class,
            official=True,
        )
        return {self.metric_id: m} if m is not None else {}

    def missing_inputs(self, item: ItemResult) -> tuple[str, ...]:
        """The declared ``required_inputs`` that ``item`` does not supply."""
        return tuple(f for f in self.required_inputs if getattr(item, f, None) is None)
