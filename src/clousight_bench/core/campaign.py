"""Campaign manifests: a live, readable progress record for a run-plan.

A ``run-plan`` runs many tasks serially, each through its own ``execute_plan``.
Every single run still lands its own auditable ``0.2`` record, but *while the
campaign is in flight* there is nothing to read but stdout and the records
trickling onto disk -- you cannot tell how many tasks are left, or which one is
running now, without grepping logs.

A :class:`CampaignManifest` fixes that. The run-plan pre-fills one manifest with
the full task list (all ``pending``) before it starts, flips each task to
``running`` then ``completed``/``failed`` as it goes, and writes the whole file
atomically on every transition. ``csbench progress`` reads that manifest -- it
never touches the executing side, so watching a campaign cannot perturb it.

The manifest is progress state, not evidence: it carries no ``record_digest``
and lives under its own ``campaigns/`` subtree so the record loaders skip it
wholesale (mirroring how ``aggregates/`` is skipped).
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from clousight_bench.core.persistence import atomic_write_text

# Manifests live in their own subtree so the record loaders skip them wholesale:
# they are progress summaries, not records.
CAMPAIGNS_DIRNAME = "campaigns"

MANIFEST_KIND = "campaign_manifest"

# Terminal task states -- a task in one of these will not change again.
TERMINAL_STATES: tuple[str, ...] = ("completed", "failed")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def new_campaign_id() -> str:
    return f"campaign-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


@dataclass
class TaskProgress:
    """One task's slot in a campaign: its status and, once done, its outcome."""

    task_id: str
    status: str = "pending"  # pending | running | completed | failed
    plan_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    elapsed_s: float | None = None
    status_counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    job_progress: dict[str, Any] = field(default_factory=dict)
    chunk_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskProgress:
        return cls(
            task_id=str(data["task_id"]),
            status=str(data.get("status", "pending")),
            plan_id=data.get("plan_id"),
            started_at=data.get("started_at"),
            ended_at=data.get("ended_at"),
            elapsed_s=data.get("elapsed_s"),
            status_counts=dict(data.get("status_counts") or {}),
            error=data.get("error"),
            job_progress=dict(data.get("job_progress") or {}),
            chunk_refs=list(data.get("chunk_refs") or []),
        )


@dataclass
class CampaignManifest:
    """A live progress record for one ``run-plan`` invocation."""

    campaign_id: str
    plan_file: str
    domain: str
    platform: str
    tasks: list[TaskProgress]
    started_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    kind: str = MANIFEST_KIND

    @property
    def total_tasks(self) -> int:
        return len(self.tasks)

    def _task(self, task_id: str) -> TaskProgress:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        raise KeyError(f"no task {task_id!r} in campaign {self.campaign_id}")

    def mark_progress(
        self,
        task_id: str,
        *,
        job_progress: dict[str, Any] | None = None,
        chunk_refs: list[str] | None = None,
    ) -> None:
        """Mirror a running task's live probe status (channel ①) into the manifest.
        Does NOT change task.status — only progress/chunk fields — so csbench
        progress can show intra-job status while the task is still running."""
        t = self._task(task_id)
        if job_progress is not None:
            t.job_progress = dict(job_progress)
        if chunk_refs is not None:
            t.chunk_refs = list(chunk_refs)
        self.updated_at = _now_iso()

    def mark_running(self, task_id: str) -> None:
        t = self._task(task_id)
        t.status = "running"
        t.started_at = _now_iso()
        self.updated_at = t.started_at

    def mark_done(
        self,
        task_id: str,
        *,
        status: str,
        plan_id: str | None = None,
        status_counts: dict[str, int] | None = None,
        error: str | None = None,
    ) -> None:
        if status not in TERMINAL_STATES:
            raise ValueError(f"status must be one of {TERMINAL_STATES}, got {status!r}")
        t = self._task(task_id)
        t.status = status
        t.plan_id = plan_id
        t.status_counts = dict(status_counts or {})
        t.error = error
        t.ended_at = _now_iso()
        if t.started_at:
            try:
                started = time.mktime(time.strptime(t.started_at, "%Y-%m-%dT%H:%M:%S%z"))
                ended = time.mktime(time.strptime(t.ended_at, "%Y-%m-%dT%H:%M:%S%z"))
                t.elapsed_s = round(ended - started, 3)
            except (ValueError, OverflowError):
                t.elapsed_s = None
        self.updated_at = t.ended_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "campaign_id": self.campaign_id,
            "plan_file": self.plan_file,
            "domain": self.domain,
            "platform": self.platform,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "total_tasks": self.total_tasks,
            "tasks": [t.to_dict() for t in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CampaignManifest:
        m = cls(
            campaign_id=str(data["campaign_id"]),
            plan_file=str(data.get("plan_file", "")),
            domain=str(data.get("domain", "")),
            platform=str(data.get("platform", "")),
            tasks=[TaskProgress.from_dict(t) for t in data.get("tasks") or []],
        )
        m.started_at = str(data.get("started_at", m.started_at))
        m.updated_at = str(data.get("updated_at", m.updated_at))
        return m


def manifest_path(results_dir: Path, campaign_id: str) -> Path:
    return Path(results_dir) / CAMPAIGNS_DIRNAME / f"{campaign_id}.json"


def write_manifest(results_dir: Path, manifest: CampaignManifest) -> Path:
    path = manifest_path(results_dir, manifest.campaign_id)
    atomic_write_text(
        path,
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return path


def load_manifest(path: Path) -> CampaignManifest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return CampaignManifest.from_dict(data)


def latest_manifest(results_dir: Path) -> Path | None:
    """The most recently updated manifest under ``results_dir/campaigns/``."""
    campaigns = Path(results_dir) / CAMPAIGNS_DIRNAME
    if not campaigns.is_dir():
        return None
    candidates = sorted(
        campaigns.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return candidates[0] if candidates else None
