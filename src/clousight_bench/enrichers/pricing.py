"""Reference cost enricher: price usage measurements into a cost estimate.

The open reference implementation of cost attribution for ResultRecord 0.4. It
reads the usage measurements a task records (names in
:data:`core.usage.USAGE_METRIC_KEYS`) and multiplies them by unit prices from a
small bundled seed of public list prices.

Additive and namespaced: everything it produces lives under
``record.extensions["pricing"]``. It never touches ``status``, ``measurements``,
``findings`` or ``errors``, so a cost estimate can never change the core's
verdict. Usage it cannot price is listed under ``uncovered`` and excluded from
``cost_usd`` -- it never invents numbers.

The mechanism is open; the data is pluggable. Point ``CLOUSIGHT_PRICING_DATA``
at a JSON file with the same schema to price against a broader / fresher feed --
the seam a managed pricing-data subscription plugs into without forking this.

Price/performance (the cloud-product selection number): a feed may carry
``system_prices`` entries -- ``{perf_metric, price, basis, provider?, region?,
source?}`` -- an operator-supplied system price (e.g. a 3-year TCO or a monthly
subscription) mapped to a headline performance measurement key. For each entry
whose ``perf_metric`` is present in the record (and whose provider/region match,
when given), the enricher emits ``price_per_unit_perf = price / value`` under
``extensions["pricing"]["price_performance"]``. The bundled seed ships NO system
prices (a price/perf composite must never come from invented numbers) and every
emitted entry is annotated unaudited -- these are official-style formulas
(price/QphH, price/tpmC) computed from the operator's own price input, not
audited results.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from clousight_bench.core.plugin import ResultEnricher
from clousight_bench.core.schema import ResultRecord
from clousight_bench.core.usage import USAGE_METRIC_KEYS

_SEED_DATA = Path(__file__).parent / "data" / "pricing.seed.json"
_DATA_ENV = "CLOUSIGHT_PRICING_DATA"
_DISCOUNT_ENV = "CLOUSIGHT_PRICING_DISCOUNTS"


def _load_feed() -> dict[str, Any]:
    override = os.environ.get(_DATA_ENV)
    path = Path(override) if override else _SEED_DATA
    return json.loads(path.read_text(encoding="utf-8"))


def _load_prices() -> list[dict[str, Any]]:
    return _load_feed()["prices"]


# Fallback when the seed has no tokens_1k entry (or is unreadable): azure /
# agent-runtime / tokens_1k list price.
_FALLBACK_TOKENS_1K_PRICE = 0.002


def tokens_1k_price() -> tuple[float, str]:
    """Return ``(price_per_1k_tokens, source)`` from the SAME price feed the
    pricing enricher uses — so it honours the ``CLOUSIGHT_PRICING_DATA`` override.

    ``source`` is ``"seed"`` when the feed supplied a ``tokens_1k`` entry,
    ``"fallback"`` otherwise. Suites' official evaluators use this for the
    ``*.cost_usd`` / ``*.cost_per_resolved`` dimensions instead of each
    re-reading the seed via a brittle relative path (which silently ignored the
    override).
    """
    try:
        for entry in _load_feed().get("prices", []):
            if entry.get("unit") == "tokens_1k" and isinstance(entry.get("price"), (int, float)):
                return float(entry["price"]), "seed"
    except Exception:  # noqa: BLE001 - unreadable feed → fallback
        pass
    return _FALLBACK_TOKENS_1K_PRICE, "fallback"


def _load_discounts() -> dict[str, Any]:
    """The private discount layer (separate from the public list feed). Default:
    no discount, so net == list and behaviour is backwards-compatible."""
    path = os.environ.get(_DISCOUNT_ENV)
    if not path:
        return {"default_pct": 0.0, "discounts": []}
    return json.loads(Path(path).read_text(encoding="utf-8"))


class PricingEnricher(ResultEnricher):
    """Price a record's usage measurements from the seed (or overridden) price list."""

    name = "pricing"

    def __init__(self) -> None:
        feed = _load_feed()
        self._prices: list[dict[str, Any]] = feed["prices"]
        # Operator-supplied system prices for price/perf composites (see module
        # docstring). The bundled seed ships none.
        self._system_prices: list[dict[str, Any]] = list(feed.get("system_prices", []))
        # Currency is whatever the price feed declares (the bundled seed is USD
        # public list price); a CNY/EUR feed flows through without code changes.
        self._currency: str = str(feed.get("currency", "USD"))
        self._discounts: dict[str, Any] = _load_discounts()

    def _resolve_discount(self, provider: str, service: str) -> float:
        """Most-specific discount wins: provider+service > provider > default > 0."""
        best_specificity = -1
        pct = float(self._discounts.get("default_pct", 0) or 0)
        for d in self._discounts.get("discounts", []):
            if d.get("provider") != provider:
                continue
            if d.get("service") and d.get("service") != service:
                continue
            specificity = 1 if d.get("service") else 0
            if specificity > best_specificity:
                best_specificity = specificity
                pct = float(d.get("pct", 0) or 0)
        return pct

    def _lookup(self, provider: str, service: str, unit: str, region: str | None) -> dict | None:
        matches = [
            p
            for p in self._prices
            if p["provider"] == provider
            and p["service"] == service
            and p["unit"] == unit
            and (not region or p["region"] == region)
        ]
        return matches[0] if matches else None

    @staticmethod
    def _measurement_value(record: ResultRecord, name: str) -> Any:
        entry = record.measurements.get(name)
        return entry.get("value") if isinstance(entry, dict) else None

    def _price_performance(self, record: ResultRecord, provider: str) -> list[dict[str, Any]]:
        """Price/perf composites from operator-supplied system prices (unaudited).

        An entry applies when its ``perf_metric`` is a numeric measurement on this
        record and its optional provider/region filters match. Nothing is ever
        invented: no matching entry, no output.
        """
        out: list[dict[str, Any]] = []
        for entry in self._system_prices:
            metric = str(entry.get("perf_metric", ""))
            value = self._measurement_value(record, metric)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                continue
            if entry.get("provider") and entry["provider"] != provider:
                continue
            if entry.get("region") and entry["region"] != record.environment.region:
                continue
            price = entry.get("price")
            if isinstance(price, bool) or not isinstance(price, (int, float)):
                continue
            out.append(
                {
                    "perf_metric": metric,
                    "perf_value": value,
                    "system_price": float(price),
                    "basis": str(entry.get("basis", "")),
                    "price_per_unit_perf": round(float(price) / float(value), 9),
                    "currency": self._currency,
                    "price_source": str(entry.get("source", "")),
                    "note": "official-style price/performance formula; unaudited; "
                    "system price supplied by the operator's price feed",
                }
            )
        return out

    def enrich(self, record: ResultRecord) -> ResultRecord:
        # Idempotent / transition guard: if a pricing plugin already priced this
        # record, leave it untouched.
        if "pricing" in record.extensions:
            return record

        provider = record.identity.adapter.split("-")[0]
        price_performance = self._price_performance(record, provider)

        # Usage-based cost: only for records that actually report usage -- never
        # annotate an unrelated result (e.g. a wordcount smoke).
        present = [u for u in USAGE_METRIC_KEYS if self._measurement_value(record, u) is not None]
        if not present:
            if price_performance:
                record.extensions["pricing"] = {
                    "currency": self._currency,
                    "price_performance": price_performance,
                }
            return record

        service = str(self._measurement_value(record, "service") or record.identity.domain)
        region = record.environment.region
        pct = self._resolve_discount(provider, service)
        breakdown: list[dict[str, Any]] = []
        uncovered: list[str] = []
        list_total = 0.0
        net_total = 0.0
        for unit in present:
            qty = self._measurement_value(record, unit)
            if isinstance(qty, bool) or not isinstance(qty, (int, float)):
                raise TypeError(
                    f"pricing: usage measurement {unit!r} must be a number, got {type(qty).__name__}: {qty!r}"
                )
            price = self._lookup(provider, service, unit, region)
            if price is None:
                uncovered.append(unit)
                continue
            # Subtotals accumulate at full precision; only the stored numbers round
            # (to 9 decimals -- nano-dollar -- so small serverless costs survive).
            list_subtotal = qty * price["price"]
            net_subtotal = list_subtotal * (1 - pct / 100.0)
            list_total += list_subtotal
            net_total += net_subtotal
            breakdown.append(
                {
                    "unit": unit,
                    "qty": qty,
                    "list_unit_price": price["price"],
                    "discount_pct": pct,
                    "list_subtotal": round(list_subtotal, 9),
                    "net_subtotal": round(net_subtotal, 9),
                    "region": price["region"],
                    "price_source": price["source"],
                    "discount_source": f"{provider}/{service}" if pct else "",
                }
            )
        list_cost = round(list_total, 9)
        net_cost = round(net_total, 9)
        pricing: dict[str, Any] = {
            "cost_usd": net_cost,
            "list_cost_usd": list_cost,
            "discount_usd": round(list_cost - net_cost, 9),
            "currency": self._currency,
            "breakdown": breakdown,
            "uncovered": uncovered,
        }
        if price_performance:
            pricing["price_performance"] = price_performance
        record.extensions["pricing"] = pricing
        return record
