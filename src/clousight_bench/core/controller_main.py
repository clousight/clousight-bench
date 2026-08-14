"""cb-controller — the on-ECS entrypoint for the ecs prod profile.

Runs on the ephemeral controller instance. Claims the campaign, drives the
serial orchestration loop (wrapping ``core.orchestrator.execute`` per task), and
lets the self-destruct watchdog reap the run (runtimes + NAT + self) on
completion/timeout/stop.

``build`` is a factory seam that wires everything from an env dict + an
``OssClient`` WITHOUT running, so it is unit-testable with no cloud. ``main`` is
the thin live entrypoint (Oss2Client via the instance metadata role).
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from clousight_bench.core.campaign_spec import DEFAULT_WATCHDOG_TIMEOUT_S
from clousight_bench.core.controller import CampaignController, RunTask, TaskOutcome
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.resource_ledger import LEDGER_FILE
from clousight_bench.core.schema import RunSpec
from clousight_bench.core.watchdog import SelfDestructWatchdog
from clousight_bench.domains.agent_runtime.probe.campaign_channel import CampaignChannel
from clousight_bench.domains.agent_runtime.probe.oss_client import OssClient

_OK_STATUSES = ("completed", "unsupported")


def build_run_task(platform: str, results_dir: str | Path, *, allow_live: bool = True) -> RunTask:
    """A RunTask that runs one task through the full lifecycle and returns its
    serialized result (JSON + optional parquet sidecar) as a TaskOutcome."""
    rdir = Path(results_dir)

    def run_task(task_id: str, spec: Any) -> TaskOutcome:
        run_spec = RunSpec(
            domain="agent-runtime",
            task_id=task_id,
            platform=platform,
            target=dict(spec.target),
            params=dict(spec.params),
        )
        record = execute(run_spec, results_dir=rdir, allow_live=allow_live)
        result_json = record.to_json().encode("utf-8")
        sidecar = (
            rdir
            / record.identity.domain
            / record.identity.adapter
            / record.run.run_id
            / "series.parquet"
        )
        parquet = sidecar.read_bytes() if sidecar.exists() else None
        ok = record.status in _OK_STATUSES
        error = None if ok else (record.status if not record.errors else str(record.errors[0]))
        return TaskOutcome(task_id=task_id, ok=ok, result_json=result_json, series_parquet=parquet, error=error)

    return run_task


def _ledger_bytes_reader(results_dir: str | Path) -> Callable[[], bytes]:
    path = Path(results_dir) / LEDGER_FILE
    return lambda: path.read_bytes() if path.exists() else b""


def build(
    env: dict[str, str],
    oss: OssClient,
    *,
    run_task: RunTask | None = None,
    reaper: Any | None = None,
    now: Callable[[], float] = time.time,
) -> tuple[CampaignController, SelfDestructWatchdog]:
    """Wire the controller + watchdog from env + OSS. No side effects."""
    campaign_id = env["CB_CAMPAIGN_ID"]
    results_dir = env.get("CB_RESULTS_DIR", "/var/lib/cb/results")
    platform = env.get("CB_PLATFORM", "aliyun-agentrun")
    channel = CampaignChannel(oss, campaign_id, now=now)

    rt = run_task or build_run_task(platform, results_dir)
    controller = CampaignController(
        channel, rt, now=now, ledger_bytes=_ledger_bytes_reader(results_dir)
    )

    spec = channel.read_launch()
    timeout_s = spec.watchdog_timeout_s if spec else DEFAULT_WATCHDOG_TIMEOUT_S
    reap = reaper.reap if reaper is not None else (lambda: None)
    watchdog = SelfDestructWatchdog(channel, reap=reap, timeout_s=timeout_s, now=now)
    return controller, watchdog


def main() -> int:  # pragma: no cover - live entrypoint, exercised by the smoke runbook
    from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client

    env = dict(os.environ)
    # In-region controller reads/writes OSS over the VPC-internal endpoint; creds
    # come from the instance RAM role via the default credential chain.
    oss = Oss2Client(env["CB_OSS_BUCKET"], env.get("CB_REGION", "cn-hangzhou"), internal=True)
    channel = CampaignChannel(oss, env["CB_CAMPAIGN_ID"])
    if not channel.claim():
        return 0  # another controller already owns this campaign
    controller, watchdog = build(env, oss)
    start = time.time()
    threading.Thread(target=controller.run, daemon=True).start()
    watchdog.run_until_terminal(start)
    return 0
