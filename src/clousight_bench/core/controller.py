"""CampaignController — the serial campaign orchestration loop (ecs prod profile).

This is "the laptop run-plan main loop, lifted into the cloud." It reads a
:class:`LaunchSpec` from OSS, runs each task SERIALLY through an injected
``run_task`` (which, in production, wraps ``core.orchestrator.execute`` +
``ResultStore``), and writes progress/results/heartbeat/ledger back to OSS after
each task. A failed task is recorded and the campaign continues; a stop sentinel
between tasks breaks the loop.

``run_task`` and ``ledger_bytes`` are injected seams so the loop is testable with
no cloud and no real orchestrator.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from clousight_bench.core.campaign_channel import CampaignChannel
from clousight_bench.core.campaign_spec import CampaignManifest, LaunchSpec, TaskEntry


@dataclass
class TaskOutcome:
    """What ``run_task`` returns for one task."""

    task_id: str
    ok: bool
    result_json: bytes
    series_parquet: bytes | None = None
    error: str | None = None
    run_id: str = ""  # unique per execution — keys the OSS result object


RunTask = Callable[[str, LaunchSpec], TaskOutcome]


class CampaignController:
    """Drive a campaign's tasks serially, sinking everything to OSS."""

    def __init__(
        self,
        channel: CampaignChannel,
        run_task: RunTask,
        *,
        now: Callable[[], float] = time.time,
        ledger_bytes: Callable[[], bytes] = lambda: b"",
    ) -> None:
        self._ch = channel
        self._run_task = run_task
        self._now = now
        self._ledger_bytes = ledger_bytes

    def run(self) -> CampaignManifest:
        spec = self._ch.read_launch()
        if spec is None:
            raise RuntimeError("no launch spec on the channel")
        manifest = CampaignManifest(
            campaign_id=spec.campaign_id,
            tasks=[TaskEntry(task_id=str(t["task_id"])) for t in spec.tasks],
        )
        self._ch.write_manifest(manifest)

        any_fail = False
        for entry in spec.tasks:
            task_id = str(entry["task_id"])
            if self._ch.stop_requested():
                break
            manifest.mark(task_id, "running", started_ts=self._now())
            self._ch.write_manifest(manifest)
            self._ch.write_heartbeat(task_id, "run")
            try:
                outcome = self._run_task(task_id, spec)
                if not outcome.ok:
                    raise RuntimeError(outcome.error or "task reported not-ok")
                # Key by task_id--run_id: a repeated task must never overwrite an
                # earlier result object (run_id is unique per execution).
                name = f"{task_id}--{outcome.run_id}" if outcome.run_id else task_id
                self._ch.write_result(name, outcome.result_json, outcome.series_parquet)
                manifest.mark(task_id, "completed", ended_ts=self._now())
            except Exception as exc:  # noqa: BLE001 — a failed task never aborts the campaign
                any_fail = True
                manifest.mark(task_id, "failed", ended_ts=self._now(), error=str(exc))
            # Sync ledger to OSS after every task so teardown never needs us alive.
            self._ch.write_ledger(self._ledger_bytes())
            self._ch.write_manifest(manifest)

        self._ch.write_done(ok=not any_fail)
        return manifest
