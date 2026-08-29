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
from pathlib import Path
from typing import Any

from clousight_bench.core.observation import Measurement
from clousight_bench.core.suite import Evaluator, RawArtifacts

_PRICING_SEED = Path(__file__).parent.parent.parent / "enrichers" / "data" / "pricing.seed.json"
_FALLBACK_TOKENS_1K_PRICE = 0.002


def _tokens_1k_price() -> tuple[float, str]:
    try:
        seed: dict[str, Any] = json.loads(_PRICING_SEED.read_text())
        for entry in seed.get("prices", []):
            if entry.get("unit") == "tokens_1k" and isinstance(entry.get("price"), (int, float)):
                return float(entry["price"]), "seed"
    except Exception:  # noqa: BLE001
        pass
    return _FALLBACK_TOKENS_1K_PRICE, "fallback"


class OfficialMmluEvaluator(Evaluator):
    """Evaluate an MMLU run: objective accuracy + managed-LLM serving dimensions."""

    evaluator_id = "official-mmlu-evaluator"
    official = True

    def supports(self, suite_id: str, product: str) -> bool:  # noqa: ARG002
        """Return True only for the ``"mmlu"`` suite."""
        return suite_id == "mmlu"

    def evaluate(self, raw: RawArtifacts) -> dict[str, Measurement]:
        """Map answers.json + summary.json → mmlu.* measurements.

        A missing/broken artifact returns ``{}`` and never raises; an absent
        latency/usage dimension is omitted (fail-safe).
        """
        try:
            answers: list[dict[str, Any]] = json.loads(raw.path("answers").read_text())
        except Exception:  # noqa: BLE001 - no answers → nothing to score
            return {}
        if not isinstance(answers, list) or not answers:
            return {}

        out: dict[str, Measurement] = {}

        # --- accuracy (objective, deterministic) ---------------------------
        total = len(answers)
        correct = sum(1 for a in answers if a.get("correct") is True)
        out["mmlu.accuracy"] = Measurement(
            value=correct / total,
            unit="ratio",
            reproducibility_class="deterministic",
            official=True,
            sample_count=total,
            notes="MMLU (cais/mmlu, MIT) 0-shot; objective multiple-choice accuracy",
        )

        # --- serving latency (environmental) -------------------------------
        latencies = [float(a["latency_ms"]) for a in answers if isinstance(a.get("latency_ms"), (int, float))]
        if latencies:
            out["mmlu.avg_latency_ms"] = Measurement(
                value=sum(latencies) / len(latencies),
                unit="ms",
                reproducibility_class="environmental",
                official=True,
                aggregation="mean",
                sample_count=len(latencies),
            )

        # --- token cost (environmental) ------------------------------------
        try:
            summary: dict[str, Any] = json.loads(raw.path("summary").read_text())
        except Exception:  # noqa: BLE001 - no summary → skip token metrics
            return out
        prompt_t = int(summary.get("prompt_tokens", 0) or 0)
        completion_t = int(summary.get("completion_tokens", 0) or 0)
        total_tokens = prompt_t + completion_t
        if total_tokens > 0:
            out["mmlu.total_tokens"] = Measurement(
                value=total_tokens,
                unit="tokens",
                reproducibility_class="environmental",
                official=True,
            )
            price_1k, source = _tokens_1k_price()
            out["mmlu.cost_usd"] = Measurement(
                value=(total_tokens / 1000.0) * price_1k,
                unit="usd",
                reproducibility_class="environmental",
                official=True,
                notes=f"tokens_1k price {price_1k} ({source})",
            )
        return out
