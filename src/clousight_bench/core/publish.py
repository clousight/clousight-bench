"""The publishing boundary: a place to send a result, and proof of the attempt.

Phase 1B ships the interface and nothing that implements it. A publisher is
injected explicitly -- it is deliberately not discovered through an entry point,
because entry-point discovery needs the API-range and conflict governance that
belongs to Phase 1D.

PUBLISH runs after PERSIST and can never rewrite the core record. Every attempt
appends one line to an append-only receipt file, so a failed upload is
recoverable evidence rather than a silent gap.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from clousight_bench.core.record import ResultRecord
from clousight_bench.core.redaction import identity_values, redact, scrub_identities

RECEIPTS_FILE = "publish-receipts.jsonl"


class ResultPublisher(ABC):
    """Send a persisted record somewhere. Must not mutate the record."""

    name: str = "abstract"

    @abstractmethod
    def publish(self, record: ResultRecord) -> dict[str, Any]:
        """Publish and return a non-secret detail dict for the receipt."""


def append_receipt(results_dir: Path, receipt: dict[str, Any]) -> Path:
    """Append one compact, scrubbed JSON line describing a publish attempt."""
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / RECEIPTS_FILE
    safe_receipt = scrub_identities(redact(receipt), identities=identity_values())
    line = json.dumps(
        safe_receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return path
