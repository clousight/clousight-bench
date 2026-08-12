"""Per-run ledger of cloud resources the harness created (and whether it deleted them).

The local index behind tag-based reconciliation. Every resource the harness
provisions is booked here keyed by run id (the same id carried in its cloud tags,
``core/resource_tags.py``); teardown marks it deleted. A resource created but not
deleted -- the crash-between-provision-and-deprovision case -- shows up as
``residual``, which the post-run reconcile reverse-looks-up and destroys.

Append-only JSONL so a crash mid-write cannot corrupt earlier entries. Best-effort
durable, not the source of truth: the authoritative record is the cloud's own
tag query (a ``ResourceReaper``); this is the fast local view that seeds it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LEDGER_FILE = ".resource_ledger.jsonl"


class ResourceLedger:
    """Append-only record of created / deleted resources under a results dir."""

    def __init__(self, results_dir: Path | str) -> None:
        self.path = Path(results_dir) / LEDGER_FILE

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def record_created(
        self,
        run_id: str,
        provider: str | None,
        resource_id: str,
        kind: str,
        tags: dict[str, Any] | None = None,
    ) -> None:
        self._append(
            {
                "event": "created",
                "run_id": run_id,
                "provider": provider or "",
                "resource_id": resource_id,
                "kind": kind,
                "tags": dict(tags or {}),
            }
        )

    def mark_deleted(self, run_id: str, resource_id: str) -> None:
        self._append(
            {
                "event": "deleted",
                "run_id": run_id,
                "resource_id": resource_id,
            }
        )

    def _events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        return events

    def residual(self, run_id: str | None = None, provider: str | None = None) -> list[dict[str, Any]]:
        """Resources created but not yet deleted, optionally scoped to a run / provider."""
        created: dict[tuple[str, str], dict[str, Any]] = {}
        for ev in self._events():
            key = (ev.get("run_id", ""), ev.get("resource_id", ""))
            if ev.get("event") == "created":
                created[key] = ev
            elif ev.get("event") == "deleted":
                created.pop(key, None)
        out = list(created.values())
        if run_id is not None:
            out = [e for e in out if e.get("run_id") == run_id]
        if provider is not None:
            out = [e for e in out if e.get("provider") == provider]
        return out
