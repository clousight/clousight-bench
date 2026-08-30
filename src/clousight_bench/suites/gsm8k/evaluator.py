"""Official GSM8K evaluator plugin.

Reads ``answers.json`` + ``summary.json`` and emits ``gsm8k.*`` measurements:
objective ``gsm8k.accuracy`` (deterministic, exact numeric match) + managed-LLM
serving dimensions (environmental). Pure function. All keys are ``gsm8k.``-
namespaced (the conformance contract for a suite's official evaluator).
"""

from __future__ import annotations

import json
from typing import Any

from clousight_bench.core.aggregate import aggregate
from clousight_bench.core.observation import ItemResult, Measurement
from clousight_bench.core.suite import Evaluator, RawArtifacts
from clousight_bench.suites._llm_shared import rows_to_items, serving_measurements

_ACC_NOTES = "GSM8K (openai/gsm8k, MIT) 0-shot; exact numeric match"


class OfficialGsm8kEvaluator(Evaluator):
    """Evaluate a GSM8K run: objective accuracy + serving dimensions."""

    evaluator_id = "official-gsm8k-evaluator"
    official = True
    extra_metric_ids = ("answered_rate",)  # add-on: fraction with a parseable answer

    def supports(self, suite_id: str, product: str) -> bool:  # noqa: ARG002
        return suite_id == "gsm8k"

    def items(self, raw: RawArtifacts) -> list[ItemResult]:
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
            output_key="predicted",
            reference_key="gold",
        )

    def evaluate(self, raw: RawArtifacts) -> dict[str, Measurement]:
        items = self.items(raw)
        if not items:
            return {}
        out: dict[str, Measurement] = {}
        acc = aggregate(items, "accuracy", "ratio", notes=_ACC_NOTES)
        if acc is not None:
            out["gsm8k.accuracy"] = acc
        try:
            answers = json.loads(raw.path("answers").read_text())
            summary: dict[str, Any] = json.loads(raw.path("summary").read_text())
        except Exception:  # noqa: BLE001 - no summary → accuracy only
            return out
        out.update(serving_measurements("gsm8k", answers, summary))
        return out
