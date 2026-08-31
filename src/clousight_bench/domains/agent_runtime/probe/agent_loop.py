"""Probe-side poller loop: consumes jobs from the blob store, runs them, writes results.

This is the container's main process when running the blob-store-mediated private
probe path.  It reads from and writes to the blob store exclusively via
:class:`BlobChannel`; there is no HTTP server or direct blob-key access here.

Key design decisions
--------------------
- Exactly ONE consumer per campaign: no concurrency/locking concerns.  Jobs are
  processed sequentially.
- ``claim()`` is best-effort: skip any job that was already claimed (e.g. after a
  container restart), so we never double-run.
- A probe that raises must produce a failed ``JobRecord`` — never crash the loop.
- The loop polls with a configurable ``poll_interval_s`` sleep between iterations
  so the blob-store API call rate stays bounded.
- ``sleep`` and ``now`` are injectable for deterministic, instant unit tests.
"""

from __future__ import annotations

import logging
import os
import time as _time
from collections.abc import Callable
from typing import Any

from clousight_bench.domains.agent_runtime.probe.blob_channel import BlobChannel
from clousight_bench.domains.agent_runtime.probe.jobs import JobProgress, JobRecord, JobSpec

log = logging.getLogger(__name__)


def run_agent_loop(
    channel: BlobChannel,
    runner: Any,
    *,
    idle_timeout_s: float = 120.0,
    poll_interval_s: float = 2.0,
    sleep: Callable[[float], None] = _time.sleep,
    now: Callable[[], float] = _time.monotonic,
    job_max_wait_s: float = 300.0,
) -> None:
    """Run the in-region probe poller until stopped or idle beyond *idle_timeout_s*.

    Args:
        channel: The :class:`BlobChannel` connecting this loop to the control plane.
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
            record = _run_job(channel, runner, job_id, spec, sleep=sleep, now=now, max_wait_s=job_max_wait_s)

            log.info("agent_loop: job %s finished status=%s", job_id, record.status)
            channel.write_result(job_id, record)

            # Reset idle timer: we just did useful work.
            idle_since = now()

        sleep(poll_interval_s)


def _run_job(
    channel: BlobChannel,
    runner: Any,
    job_id: str,
    spec: JobSpec,
    *,
    sleep: Callable[[float], None] = _time.sleep,
    now: Callable[[], float] = _time.monotonic,
    max_wait_s: float = 300.0,
) -> JobRecord:
    """Submit *spec* to the runner, relay progress to *channel*, return terminal record.

    The runner's ``submit()`` starts a background thread and returns a *runner*
    job ID immediately.  We then poll ``runner.get()`` until the status is terminal,
    calling ``channel.write_progress()`` for every new progress snapshot.  The
    terminal record from the runner is the authoritative result.

    If ``runner.submit`` or the subsequent poll raises for any reason, we return a
    synthetic ``JobRecord`` with status="failed" so the control plane always receives
    a terminal object.

    Args:
        channel: The :class:`BlobChannel` used for relaying progress and results.
        runner: Duck-typed runner with ``submit`` / ``get`` methods.
        job_id: Channel-level job ID (used to key blob-store objects).
        spec: The probe specification to run.
        sleep: Replacement for :func:`time.sleep`; injected in tests.
        now: Replacement for :func:`time.monotonic`; injected in tests.
        max_wait_s: Give up and return a failed record if the runner's background
            thread has not reached a terminal status within this many seconds.
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
    deadline = now() + max_wait_s
    last_progress_written: JobProgress | None = None

    while True:
        record = runner.get(runner_job_id)
        if record is None:
            # Should not happen with a well-behaved runner; treat as failed.
            return JobRecord(
                job_id=job_id,
                status="failed",
                error="runner.get() returned None for submitted job",
            )

        # Relay progress to the channel only when a new snapshot is available
        # (best-effort; never crash the loop).
        try:
            if record.progress is not None and record.progress != last_progress_written:
                channel.write_progress(job_id, record.progress, record.live_metrics)
                last_progress_written = record.progress
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

        # Timeout guard: if the runner's background thread never reaches a terminal
        # status, give up rather than looping forever.
        if now() >= deadline:
            return JobRecord(
                job_id=job_id,
                status="failed",
                error=f"probe did not complete within {max_wait_s}s",
            )

        # Not terminal yet — runner is still in a background thread.
        sleep(0.01)


def main() -> None:
    """Entry point for the in-region probe host.

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
    # A whole-campaign carrier must outlive the gaps between data-plane jobs
    # (control-plane tasks like provisioning dispatch none). The control
    # plane sets this to span the campaign; default stays short for one-shot use.
    idle_timeout_s = float(os.environ.get("CB_PROBE_IDLE_TIMEOUT", "120"))
    # Per-job execution cap. Long probes (warm-keepalive, elasticity, sustained
    # load) with a slow AgentRuntime blow past the 300s default — the control
    # plane sets this to match its own BlobProbeClient timeout.
    job_max_wait_s = float(os.environ.get("CB_PROBE_JOB_MAX_WAIT", "300"))

    from clousight_bench.domains.agent_runtime.probe.oss_client import EcsRamRoleOssClient

    store = EcsRamRoleOssClient(bucket=bucket, region=region)
    channel = BlobChannel(store, campaign_id=campaign_id)

    from clousight_bench.domains.agent_runtime.probe.server import build_default_runner

    runner = build_default_runner()

    run_agent_loop(channel, runner, idle_timeout_s=idle_timeout_s, job_max_wait_s=job_max_wait_s)


if __name__ == "__main__":
    main()
