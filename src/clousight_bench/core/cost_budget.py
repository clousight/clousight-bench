"""Cumulative cost budget: stop runaway real-cloud spend at a threshold.

The live gate (``core/live_guard.py``) stops *accidental* spend -- a live run
needs explicit acknowledgement. This stops *runaway* spend: a budget caps the
total realized cost across every run that shares a results dir. Before a live run
the orchestrator checks the spend-so-far plus this run's estimate; crossing the
budget blocks the run (``cost.budget_exceeded``) before anything is provisioned.
After a run, its realized cost is added to the ledger.

Realized cost comes from the pricing enricher (``extensions["pricing"]
["cost_usd"]``); with no pricing installed it falls back to a caller-supplied
``target["estimated_cost_usd"]`` so the cap still works, just coarser.

The ledger is a small JSON file under the results dir. It is best-effort
durable, not a billing system: the authoritative meter is the cloud's own bill.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LEDGER_FILE = ".cost_ledger.json"


def run_cost_usd(record: Any, target: dict[str, Any] | None = None) -> float:
    """This run's cost: the priced net cost if the enricher ran, else the
    caller's ``estimated_cost_usd`` (default 0)."""
    extensions = getattr(record, "extensions", None) or {}
    priced = extensions.get("pricing", {}).get("cost_usd")
    if isinstance(priced, (int, float)) and not isinstance(priced, bool):
        return float(priced)
    return float((target or {}).get("estimated_cost_usd", 0.0) or 0.0)


def budget_would_exceed(spent: float, estimate: float, budget: float | None) -> bool:
    """True if running one more estimated-``estimate`` run would cross ``budget``."""
    if budget is None:
        return False
    return (spent + estimate) >= budget


class CostLedger:
    """Append-only cumulative cost total, persisted under a results dir."""

    def __init__(self, results_dir: Path | str) -> None:
        self.path = Path(results_dir) / LEDGER_FILE

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"total_usd": 0.0, "entries": []}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"total_usd": 0.0, "entries": []}

    def total(self) -> float:
        return float(self._load().get("total_usd", 0.0) or 0.0)

    def add(self, run_id: str, provider: str | None, cost_usd: float) -> float:
        """Record one run's cost and return the new cumulative total."""
        data = self._load()
        data["total_usd"] = round(float(data.get("total_usd", 0.0) or 0.0) + cost_usd, 9)
        data.setdefault("entries", []).append(
            {"run_id": run_id, "provider": provider or "", "cost_usd": round(cost_usd, 9)}
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data["total_usd"]
