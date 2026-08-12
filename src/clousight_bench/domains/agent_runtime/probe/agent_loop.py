"""ECI-side poller loop: consumes probe jobs from OSS, runs them, writes results.

This is the container's main process when running the OSS-mediated private probe
path.  It reads from and writes to OSS exclusively via :class:`OssChannel`; there
is no HTTP server or direct OSS key access here.

Key design decisions
--------------------
- Exactly ONE consumer per campaign: no concurrency/locking concerns.  Jobs are
  processed sequentially.
- ``claim()`` is best-effort: skip any job that was already claimed (e.g. after a
  container restart), so we never double-run.
- A probe that raises must produce a failed ``JobRecord`` — never crash the loop.
- The loop polls with a configurable ``poll_interval_s`` sleep between iterations
  so the OSS API call rate stays bounded.
- ``sleep`` and ``now`` are injectable for deterministic, instant unit tests.
"""

from __future__ import annotations

import logging
import os
import time as _time
from collections.abc import Callable
from typing import Any

from clousight_bench.domains.agent_runtime.probe.jobs import JobRecord, JobSpec
from clousight_bench.domains.agent_runtime.probe.oss_channel import OssChannel

log = logging.getLogger(__name__)


def run_agent_loop(
    channel: OssChannel,
    runner: Any,
    *,
    idle_timeout_s: float = 120.0,
    poll_interval_s: float = 2.0,
    sleep: Callable[[float], None] = _time.sleep,
    now: Callable[[], float] = _time.monotonic,
) -> None:
    """Run the ECI poller until stopped or idle beyond *idle_timeout_s*.

    Args:
        channel: The :class:`OssChannel` connecting this loop to the control plane.
        runner: A :class:`~clousight_bench.domains.agent_runtime.probe.runner.JobRunner`
            (or duck-typed equivalent with ``submit(spec) -> job_id`` and
            ``get(job_id) -> JobRecord | None``).
        idle_timeout_s: Exit after this many seconds with no jobs dispatched.
        poll_interval_s: Seconds to sleep between poll iterations.
        sleep: Replacement for :func:`time.sleep`; injected in tests.
        now: Replacement for :func:`time.monotonic`; injected in tests.
    """
    log.info("agent_loop: starting, writing ready marker")
    channel.write_ready()

    idle_since: float = now()

    while True:
        # --- Stop check ---
        if channel.stop_requested():
            log.info("agent_loop: stop requested, exiting")
            return

        # --- Idle timeout check ---
        if now() - idle_since >= idle_timeout_s:
            log.info("agent_loop: idle timeout reached, exiting")
            return

        # --- Process pending jobs ---
        pending = channel.list_pending_jobs()
        if not pending:
            sleep(poll_interval_s)
            continue

        for job_id in pending:
            # Best-effort claim: skip if already claimed.
            if not channel.claim(job_id):
                log.debug("agent_loop: job %s already claimed, skipping", job_id)
                continue

            try:
                spec = channel.read_job(job_id)
            except KeyError:
                log.warning("agent_loop: job %s spec missing after claim, skipping", job_id)
                continue

            log.info("agent_loop: running job %s (probe=%s)", job_id, spec.probe)
            record = _run_job(channel, runner, job_id, spec)

            log.info("agent_loop: job %s finished status=%s", job_id, record.status)
            channel.write_result(job_id, record)

            # Reset idle timer: we just did useful work.
            idle_since = now()

        sleep(poll_interval_s)


def _run_job(
    channel: OssChannel,
    runner: Any,
    job_id: str,
    spec: JobSpec,
) -> JobRecord:
    """Submit *spec* to the runner, relay progress to *channel*, return terminal record.

    The runner's ``submit()`` starts a background thread and returns a *runner*
    job ID immediately.  We then poll ``runner.get()`` until the status is terminal,
    calling ``channel.write_progress()`` for every new progress snapshot.  The
    terminal record from the runner is the authoritative result.

    If ``runner.submit`` or the subsequent poll raises for any reason, we return a
    synthetic ``JobRecord`` with status="failed" so the control plane always receives
    a terminal object.
    """
    try:
        runner_job_id = runner.submit(spec)
    except Exception as exc:  # noqa: BLE001
        return JobRecord(
            job_id=job_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )

    # Poll until terminal, relaying progress snapshots.
    while True:
        record = runner.get(runner_job_id)
        if record is None:
            # Should not happen with a well-behaved runner; treat as failed.
            return JobRecord(
                job_id=job_id,
                status="failed",
                error="runner.get() returned None for submitted job",
            )

        # Relay progress to the channel (best-effort; never crash the loop).
        try:
            channel.write_progress(job_id, record.progress, record.live_metrics)
        except Exception:  # noqa: BLE001
            pass

        if record.status in ("completed", "failed"):
            # Return a record stamped with the *channel* job_id, not the internal one.
            return JobRecord(
                job_id=job_id,
                status=record.status,
                progress=record.progress,
                live_metrics=record.live_metrics,
                observations=record.observations,
                error=record.error,
                chunk_refs=list(record.chunk_refs),
            )

        # Not terminal yet — runner is still in a background thread.
        _time.sleep(0.01)


def main() -> None:
    """Entry point for the ECI container.

    Reads configuration from environment variables:

    - ``CB_PROBE_BUCKET``: OSS bucket name (required).
    - ``CB_PROBE_REGION``: Aliyun region ID, e.g. ``cn-hangzhou`` (required).
    - ``CB_PROBE_CONTROL_PREFIX``: per-campaign control prefix, i.e. the
      *campaign_id* component (required).
    - ``CB_PROBE_TOKEN``: optional bearer token (unused by the loop itself; kept
      for parity with the HTTP probe's env contract).
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    bucket = os.environ["CB_PROBE_BUCKET"]
    region = os.environ["CB_PROBE_REGION"]
    campaign_id = os.environ["CB_PROBE_CONTROL_PREFIX"]

    from clousight_bench.domains.agent_runtime.probe.oss_client import EcsRamRoleOssClient

    oss = EcsRamRoleOssClient(bucket=bucket, region=region)
    channel = OssChannel(oss, campaign_id=campaign_id)

    from clousight_bench.domains.agent_runtime.probe.server import build_default_runner

    runner = build_default_runner()

    run_agent_loop(channel, runner)


if __name__ == "__main__":
    main()
