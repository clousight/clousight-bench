"""Official MMLU evaluator plugin.

Reads ``answers.json`` + ``summary.json`` from :class:`RawArtifacts` and returns
namespaced measurements. Pure function — no cloud, no credentials.

- ``mmlu.accuracy``: correct / total — the recognized MMLU metric, objective and
  deterministic (``official=True``).
- ``mmlu.total_tokens`` / ``mmlu.cost_usd`` / ``mmlu.avg_latency_ms``: serving
  dimensions of the managed LLM (``environmental``, ``official=True`` provenance).
  Cost = tokens × the seed price (same source as the pricing enricher); omitted
  if no usage was recorded. (All keys are ``mmlu.``-namespaced — the conformance
  contract requires a suite's official evaluator to emit only ``<suite_id>.`` keys.)
"""

from __future__ import annotations

import json
from typing import Any

from clousight_bench.core.aggregate import aggregate, aggregate_by_group
from clousight_bench.core.observation import ItemResult, Measurement
from clousight_bench.core.suite import Evaluator, RawArtifacts
from clousight_bench.suites.llm_common import rows_to_items, serving_measurements

_ACC_NOTES = "MMLU (cais/mmlu, MIT) 0-shot; objective multiple-choice accuracy"


class OfficialMmluEvaluator(Evaluator):
    """Evaluate an MMLU run: objective accuracy + managed-LLM serving dimensions."""

    evaluator_id = "official-mmlu-evaluator"
    official = True
    extra_metric_ids = ("answered_rate",)  # add-on: fraction with a parseable answer

    def supports(self, suite_id: str, product: str) -> bool:  # noqa: ARG002
        """Return True only for the ``"mmlu"`` suite."""
        return suite_id == "mmlu"

    def items(self, raw: RawArtifacts) -> list[ItemResult]:
        """Per-item substrate: one ItemResult per question, grouped by subject."""
        try:
            answers: list[dict[str, Any]] = json.loads(raw.path("answers").read_text())
        except Exception:  # noqa: BLE001 - no answers → no items
            return []
        if not isinstance(answers, list):
            return []
        return rows_to_items(
            answers,
            metric="accuracy",
            id_key="id",
            correct_key="correct",
            group_key="subject",
            output_key="predicted",
            reference_key="gold",
        )

    def evaluate(self, raw: RawArtifacts) -> dict[str, Measurement]:
        """Aggregate per-item scores → mmlu.* measurements.

        ``mmlu.accuracy`` is the mean of the per-item accuracy scores (identical
        correct/total ratio as before, now with a Wilson CI + a per-subject
        breakdown under ``mmlu.accuracy.by_group.<subject>``). A missing/broken
        artifact returns ``{}`` and never raises (fail-safe).
        """
        items = self.items(raw)
        if not items:
            return {}
        out: dict[str, Measurement] = {}
        acc = aggregate(items, "accuracy", "ratio", notes=_ACC_NOTES)
        if acc is not None:
            out["mmlu.accuracy"] = acc
        for group, m in aggregate_by_group(items, "accuracy", "ratio").items():
            out[f"mmlu.accuracy.by_group.{group}"] = m

        # --- serving dimensions (latency / tokens / cost, environmental) ----
        try:
            answers = json.loads(raw.path("answers").read_text())
            summary: dict[str, Any] = json.loads(raw.path("summary").read_text())
        except Exception:  # noqa: BLE001 - no summary → accuracy only
            return out
        out.update(serving_measurements("mmlu", answers, summary))
        return out
