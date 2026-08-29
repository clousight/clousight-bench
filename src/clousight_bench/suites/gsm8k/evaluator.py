"""Official GSM8K evaluator plugin.

Reads ``answers.json`` + ``summary.json`` and emits ``gsm8k.*`` measurements:
objective ``gsm8k.accuracy`` (deterministic, exact numeric match) + managed-LLM
serving dimensions (environmental). Pure function. All keys are ``gsm8k.``-
namespaced (the conformance contract for a suite's official evaluator).
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


class OfficialGsm8kEvaluator(Evaluator):
    """Evaluate a GSM8K run: objective accuracy + serving dimensions."""

    evaluator_id = "official-gsm8k-evaluator"
    official = True

    def supports(self, suite_id: str, product: str) -> bool:  # noqa: ARG002
        return suite_id == "gsm8k"

    def evaluate(self, raw: RawArtifacts) -> dict[str, Measurement]:
        try:
            answers: list[dict[str, Any]] = json.loads(raw.path("answers").read_text())
        except Exception:  # noqa: BLE001
            return {}
        if not isinstance(answers, list) or not answers:
            return {}

        out: dict[str, Measurement] = {}
        total = len(answers)
        correct = sum(1 for a in answers if a.get("correct") is True)
        out["gsm8k.accuracy"] = Measurement(
            value=correct / total,
            unit="ratio",
            reproducibility_class="deterministic",
            official=True,
            sample_count=total,
            notes="GSM8K (openai/gsm8k, MIT) 0-shot; exact numeric match",
        )
        latencies = [float(a["latency_ms"]) for a in answers if isinstance(a.get("latency_ms"), (int, float))]
        if latencies:
            out["gsm8k.avg_latency_ms"] = Measurement(
                value=sum(latencies) / len(latencies),
                unit="ms",
                reproducibility_class="environmental",
                official=True,
                aggregation="mean",
                sample_count=len(latencies),
            )
        try:
            summary: dict[str, Any] = json.loads(raw.path("summary").read_text())
        except Exception:  # noqa: BLE001
            return out
        total_tokens = int(summary.get("prompt_tokens", 0) or 0) + int(
            summary.get("completion_tokens", 0) or 0
        )
        if total_tokens > 0:
            out["gsm8k.total_tokens"] = Measurement(
                value=total_tokens,
                unit="tokens",
                reproducibility_class="environmental",
                official=True,
            )
            price_1k, source = _tokens_1k_price()
            out["gsm8k.cost_usd"] = Measurement(
                value=(total_tokens / 1000.0) * price_1k,
                unit="usd",
                reproducibility_class="environmental",
                official=True,
                notes=f"tokens_1k price {price_1k} ({source})",
            )
        return out
