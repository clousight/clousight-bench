"""Reference cost enricher: turn resource-usage metrics into a cost estimate.

The open reference implementation of cost attribution. It multiplies the usage
metrics a task records (see :data:`core.usage.USAGE_METRIC_KEYS`) by unit prices
from a small bundled seed price list (public list prices, dated). It never
invents numbers: usage it cannot price is reported in notes and excluded from
``cost_usd``.

The mechanism is open; the *data* is pluggable. Point ``CLOUSIGHT_PRICING_DATA``
at a JSON file with the same schema to price against a broader / fresher /
negotiated feed -- the seam a managed pricing-data subscription plugs into
without forking this enricher.
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


def _load_prices() -> list[dict[str, Any]]:
    override = os.environ.get(_DATA_ENV)
    path = Path(override) if override else _SEED_DATA
    return json.loads(path.read_text(encoding="utf-8"))["prices"]


class PricingEnricher(ResultEnricher):
    """Price a ResultRecord's usage metrics from the seed (or overridden) price list."""

    name = "pricing"

    def __init__(self) -> None:
        self._prices: list[dict[str, Any]] = _load_prices()

    def _lookup(self, provider: str, service: str, unit: str, region: str | None) -> dict | None:
        matches = [
            p for p in self._prices
            if p["provider"] == provider and p["service"] == service and p["unit"] == unit
            and (region is None or p["region"] == region)
        ]
        return matches[0] if matches else None

    def enrich(self, record: ResultRecord) -> ResultRecord:
        # Idempotency / transition guard: if another enricher already priced this
        # record (e.g. a commercial pricing pack), leave it untouched.
        if "cost_usd" in record.metrics:
            return record
        # Only touch records that actually report usage -- never annotate an
        # unrelated result (e.g. a wordcount smoke) with a spurious cost.
        present = [u for u in USAGE_METRIC_KEYS if record.metrics.get(u) is not None]
        if not present:
            return record

        provider = record.platform.split("-")[0]
        service = str(record.metrics.get("service", record.domain))
        region = record.metrics.get("region")
        breakdown: list[dict[str, Any]] = []
        uncovered: list[str] = []
        total = 0.0
        for unit in present:
            qty = record.metrics.get(unit)
            if isinstance(qty, bool) or not isinstance(qty, (int, float)):
                raise TypeError(
                    f"pricing: usage metric {unit!r} must be a number, "
                    f"got {type(qty).__name__}: {qty!r}"
                )
            price = self._lookup(provider, service, unit, region)
            if price is None:
                uncovered.append(unit)
                continue
            subtotal = round(qty * price["price"], 6)
            total += subtotal
            breakdown.append({
                "unit": unit, "qty": qty, "unit_price": price["price"],
                "subtotal": subtotal, "region": price["region"], "price_source": price["source"],
            })
        record.metrics["cost_usd"] = round(total, 6)
        record.raw["pricing_breakdown"] = breakdown
        if uncovered:
            note = f"pricing: uncovered units for {provider}/{service}: {', '.join(uncovered)}"
            record.notes = (record.notes + " | " + note).strip(" |")
        return record
