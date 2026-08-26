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
from pathlib import Path
from typing import Any

from clousight_bench.core.observation import Measurement
from clousight_bench.core.suite import Evaluator, RawArtifacts

# Path to the bundled pricing seed (relative to this file's location).
_PRICING_SEED = Path(__file__).parent.parent.parent / "enrichers" / "data" / "pricing.seed.json"

# Fallback price: azure / agent-runtime / tokens_1k = 0.002 USD / 1k tokens.
_FALLBACK_TOKENS_1K_PRICE: float = 0.002


def _load_tokens_1k_price() -> tuple[float, str]:
    """Return ``(price, source)`` for the first ``tokens_1k`` entry found in the seed.

    ``source`` is ``"seed"`` when the pricing seed was read successfully,
    ``"fallback"`` when the seed is absent or contains no usable entry.
    """
    try:
        seed: dict[str, Any] = json.loads(_PRICING_SEED.read_text())
        for entry in seed.get("prices", []):
            if entry.get("unit") == "tokens_1k":
                price = entry.get("price")
                if isinstance(price, (int, float)):
                    return float(price), "seed"
    except Exception:  # noqa: BLE001
        pass
    return _FALLBACK_TOKENS_1K_PRICE, "fallback"


class OfficialSweEvaluator(Evaluator):
    """Evaluate SWE-bench Verified runs from the official upstream harness.

    Resolved ratio = resolved_instances / total_instances, matching
    swebench.com leaderboard semantics.
    """

    evaluator_id = "official-swe-evaluator"
    official = True

    def supports(self, suite_id: str, product: str) -> bool:  # noqa: ARG002
        """Return True only for the ``"swe-bench"`` suite."""
        return suite_id == "swe-bench"

    def evaluate(self, raw: RawArtifacts) -> dict[str, Measurement]:
        """Evaluate a SWE-bench run from its artifacts.

        Returns a dict with ``swe-bench.resolved`` always present, and
        ``swe-bench.cost_per_resolved`` when usage data is available and
        every usage line is well-formed (no malformed lines are tolerated;
        if any line is malformed the cost dimension is omitted entirely).
        """
        # --- resolved ratio ------------------------------------------------
        results_data: dict[str, Any] = json.loads(raw.path("results").read_text())
        total: int = int(results_data.get("total", 0))
        resolved: int = int(results_data.get("resolved", 0))

        ratio = resolved / total if total > 0 else 0.0

        out: dict[str, Measurement] = {
            "swe-bench.resolved": Measurement(
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
                price_per_1k, source = _load_tokens_1k_price()
                total_cost = (total_tokens / 1000.0) * price_per_1k
                out["swe-bench.cost_per_resolved"] = Measurement(
                    value=total_cost / resolved,
                    unit="usd",
                    reproducibility_class="environmental",
                    official=True,
                    notes=f"tokens_1k price {price_per_1k} ({source})",
                )

        return out
