"""Campaign launch spec + progress manifest for the ecs prod profile.

A ``LaunchSpec`` is what the laptop ``submit`` writes to OSS; the controller reads
it to know the task-set, params, target, and self-destruct timeout. A
``CampaignManifest`` is the controller's progress record (one ``TaskEntry`` per
task, flipped pending→running→completed/failed), read back by ``status``.

Both serialize to/from plain JSON bytes so they travel as OSS objects.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

# Default self-destruct window: a full 27-task serial run is ~70-120min; 90min
# gives headroom before the watchdog reaps.
DEFAULT_WATCHDOG_TIMEOUT_S = 5400.0


@dataclass
class LaunchSpec:
    """The campaign launch request written by ``submit``, read by the controller."""

    campaign_id: str
    tasks: list[str]
    params: dict[str, Any]
    target: dict[str, Any]
    watchdog_timeout_s: float = DEFAULT_WATCHDOG_TIMEOUT_S

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> LaunchSpec:
        d = json.loads(data)
        return cls(
            campaign_id=str(d["campaign_id"]),
            tasks=list(d["tasks"]),
            params=dict(d.get("params") or {}),
            target=dict(d.get("target") or {}),
            watchdog_timeout_s=float(d.get("watchdog_timeout_s", DEFAULT_WATCHDOG_TIMEOUT_S)),
        )


@dataclass
class TaskEntry:
    """One task's progress within a campaign."""

    task_id: str
    status: str = "pending"  # pending | running | completed | failed
    started_ts: float | None = None
    ended_ts: float | None = None
    error: str | None = None


@dataclass
class CampaignManifest:
    """Ordered progress record for a campaign; the controller mutates it in place."""

    campaign_id: str
    tasks: list[TaskEntry]

    def mark(self, task_id: str, status: str, **fields: Any) -> None:
        """Flip one task's status (+ optional fields) — leaves every other entry
        untouched (partial-update isolation)."""
        for t in self.tasks:
            if t.task_id == task_id:
                t.status = status
                for k, v in fields.items():
                    setattr(t, k, v)
                return
        raise KeyError(f"unknown task_id {task_id!r}")

    def counts(self) -> dict[str, int]:
        return dict(Counter(t.status for t in self.tasks))

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_json(cls, data: bytes) -> CampaignManifest:
        d = json.loads(data)
        return cls(
            campaign_id=str(d["campaign_id"]),
            tasks=[
                TaskEntry(
                    task_id=str(e["task_id"]),
                    status=str(e.get("status", "pending")),
                    started_ts=e.get("started_ts"),
                    ended_ts=e.get("ended_ts"),
                    error=e.get("error"),
                )
                for e in d.get("tasks", [])
            ],
        )
