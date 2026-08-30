"""Official SWE-bench evaluator plugin.

Reads ``results.json`` and optionally ``usage.jsonl`` from :class:`RawArtifacts`
and returns namespaced :class:`Measurement` objects under the ``swe-bench.`` prefix.

Registered via the ``clousight_bench.evaluators`` entry-point group as
``official-swe-evaluator``.

Resolved ratio semantics: resolved ratio = resolved_instances / total_instances,
matching swebench.com leaderboard semantics.
"""

from __future__ import annotations

import json
from typing import Any

from clousight_bench.core.observation import Measurement
from clousight_bench.core.suite import Evaluator, RawArtifacts
from clousight_bench.enrichers.pricing import tokens_1k_price


class OfficialSweEvaluator(Evaluator):
    """Evaluate SWE-bench Verified runs from the official upstream harness.

    Resolved ratio = resolved_instances / total_instances, matching
    swebench.com leaderboard semantics.
    """

    evaluator_id = "official-swe-evaluator"
    official = True

    # Suite this evaluator serves; also the measurement namespace prefix.
    # A variant evaluator (e.g. SWE-bench Lite) is a thin subclass that only
    # overrides ``evaluator_id`` and ``suite_id`` — the SWE-bench harness report
    # shape is identical across splits, so the parsing logic below is shared.
    suite_id = "swe-bench"

    def supports(self, suite_id: str, product: str) -> bool:  # noqa: ARG002
        """Return True only for this evaluator's own ``suite_id``."""
        return suite_id == self.suite_id

    def evaluate(self, raw: RawArtifacts) -> dict[str, Measurement]:
        """Evaluate a SWE-bench run from its artifacts.

        Returns a dict with ``<suite_id>.resolved`` present whenever results are
        readable, and ``<suite_id>.cost_per_resolved`` when usage data is
        available and every usage line is well-formed (no malformed lines are
        tolerated; if any line is malformed the cost dimension is omitted
        entirely). A missing/corrupt ``results.json`` returns ``{}`` rather than
        raising (fail-safe, matching the mmlu/gsm8k/human-eval evaluators).
        """
        # --- resolved ratio ------------------------------------------------
        try:
            results_data: dict[str, Any] = json.loads(raw.path("results").read_text())
        except Exception:  # noqa: BLE001 - no readable results → nothing to score
            return {}
        total: int = int(results_data.get("total", 0))
        resolved: int = int(results_data.get("resolved", 0))

        ratio = resolved / total if total > 0 else 0.0

        out: dict[str, Measurement] = {
            f"{self.suite_id}.resolved": Measurement(
                value=ratio,
                unit="ratio",
                reproducibility_class="deterministic",
                official=True,
            )
        }

        # --- cost per resolved (optional) -----------------------------------
        if "usage" in raw.manifest and resolved > 0:
            usage_path = raw.path("usage")
            total_tokens: int = 0
            malformed: int = 0

            for line in usage_path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if not isinstance(record, dict):
                    malformed += 1
                    continue
                if record.get("kind") == "llm_tokens":
                    raw_value = record.get("value", 0)
                    try:
                        total_tokens += int(raw_value)
                    except (TypeError, ValueError):
                        malformed += 1

            if malformed == 0 and total_tokens > 0:
                price_per_1k, source = tokens_1k_price()
                total_cost = (total_tokens / 1000.0) * price_per_1k
                out[f"{self.suite_id}.cost_per_resolved"] = Measurement(
                    value=total_cost / resolved,
                    unit="usd",
                    reproducibility_class="environmental",
                    official=True,
                    notes=f"tokens_1k price {price_per_1k} ({source})",
                )

        return out
