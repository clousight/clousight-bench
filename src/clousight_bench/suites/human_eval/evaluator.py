"""Official HumanEval evaluator plugin.

Reads ``results.json`` + ``summary.json`` from :class:`RawArtifacts` and returns
namespaced measurements. Pure function — no cloud, no credentials.

- ``human-eval.pass_at_1``: problems_passed / total — the recognized HumanEval
  ``pass@1`` metric with one sample per problem.  Objective and deterministic
  (``official=True``): a candidate either passes its unit test or it does not.
- ``human-eval.total_tokens`` / ``human-eval.cost_usd`` / ``human-eval.avg_latency_ms``:
  serving dimensions of the managed LLM (``environmental``, ``official=True``
  provenance).  Omitted when no usage/latency was recorded (e.g. the reference or
  fixture path).  (All keys are ``human-eval.``-namespaced — the conformance
  contract requires a suite's official evaluator to emit only ``<suite_id>.`` keys.)
"""

from __future__ import annotations

import json
from typing import Any

from clousight_bench.core.aggregate import aggregate
from clousight_bench.core.observation import ItemResult, Measurement
from clousight_bench.core.suite import Evaluator, RawArtifacts
from clousight_bench.suites._llm_shared import rows_to_items, serving_measurements

_PASS_NOTES = "HumanEval (openai/openai_humaneval, MIT) pass@1; unit-test execution"


class OfficialHumanEvalEvaluator(Evaluator):
    """Evaluate a HumanEval run: pass@1 correctness + managed-LLM serving dimensions."""

    evaluator_id = "official-humaneval-evaluator"
    official = True

    def supports(self, suite_id: str, product: str) -> bool:  # noqa: ARG002
        """Return True only for the ``"human-eval"`` suite."""
        return suite_id == "human-eval"

    def items(self, raw: RawArtifacts) -> list[ItemResult]:
        try:
            results: list[dict[str, Any]] = json.loads(raw.path("results").read_text())
        except Exception:  # noqa: BLE001 - no results → no items
            return []
        if not isinstance(results, list):
            return []
        return rows_to_items(
            results,
            metric="pass_at_1",
            id_key="task_id",
            correct_key="passed",
        )

    def evaluate(self, raw: RawArtifacts) -> dict[str, Measurement]:
        """Aggregate per-item pass/fail → human-eval.* measurements.

        A missing/broken artifact returns ``{}`` and never raises (fail-safe).
        """
        items = self.items(raw)
        if not items:
            return {}
        out: dict[str, Measurement] = {}
        m = aggregate(items, "pass_at_1", "ratio", notes=_PASS_NOTES)
        if m is not None:
            out["human-eval.pass_at_1"] = m

        # --- serving dimensions (latency / tokens / cost, environmental) ----
        try:
            results = json.loads(raw.path("results").read_text())
            summary: dict[str, Any] = json.loads(raw.path("summary").read_text())
        except Exception:  # noqa: BLE001 - no summary → pass@1 only
            return out
        out.update(serving_measurements("human-eval", results, summary))
        return out
