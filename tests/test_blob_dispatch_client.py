"""Tests for BlobProbeClient — the control-plane blob-store dispatch client.

All tests are deterministic, offline (InMemoryBlobStore), and fast (injected
clock; no real sleep or network).

Test cases:
  (a) happy path: ECI thread reads pending job, writes progress + result → ObservationBundle;
      on_progress fires.
  (b) failed job → ProbeJobFailed with record.error.
  (c) timeout: no result → ProbeJobFailed with timeout message (injected clock, no thread needed).
  (d) progress dedup: identical progress snapshot does not double-invoke on_progress.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from clousight_bench.core.blobstore import InMemoryBlobStore
from clousight_bench.domains.agent_runtime.probe.blob_channel import BlobChannel
from clousight_bench.domains.agent_runtime.probe.blob_dispatch_client import BlobProbeClient
from clousight_bench.domains.agent_runtime.probe.client import ProbeJobFailed
from clousight_bench.domains.agent_runtime.probe.jobs import (
    JobProgress,
    JobRecord,
    JobSpec,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store() -> InMemoryBlobStore:
    return InMemoryBlobStore()


@pytest.fixture()
def control_channel(store: InMemoryBlobStore) -> BlobChannel:
    return BlobChannel(store, campaign_id="camp-test")


@pytest.fixture()
def eci_channel(store: InMemoryBlobStore) -> BlobChannel:
    """Same underlying store as control_channel — simulates the ECI side."""
    return BlobChannel(store, campaign_id="camp-test")


@pytest.fixture()
def spec() -> JobSpec:
    return JobSpec(
        probe="ttft",
        params={"samples": 2},
        target_endpoint="https://api.example.com/ep",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _completed_record(job_id: str) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        status="completed",
        observations={
            "observations": {"ttft_ms": 120.0},
            "series": {},
            "artifacts": [],
        },
    )


def _failed_record(job_id: str, error: str) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        status="failed",
        error=error,
    )


def _spin_eci(eci: BlobChannel, make_record: Any, progress: JobProgress | None = None) -> None:
    """Spin in a thread until a pending job appears, optionally write progress, then write the record."""

    def _run() -> None:
        for _ in range(200):
            pending = eci.list_pending_jobs()
            if pending:
                job_id = pending[0]
                if progress is not None:
                    eci.write_progress(job_id, progress, {"latency_ms": 55.0})
                record = make_record(job_id)
                eci.write_result(job_id, record)
                return
            time.sleep(0.005)  # real sleep OK here — ECI thread, not the client

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# (a) Happy path: completed job → ObservationBundle; on_progress fires
# ---------------------------------------------------------------------------


def test_happy_path_returns_observation_bundle_and_fires_progress(
    control_channel: BlobChannel,
    eci_channel: BlobChannel,
    spec: JobSpec,
) -> None:
    """run_job returns ObservationBundle from a completed JobRecord; on_progress fires."""
    progress_calls: list[tuple[JobProgress, dict[str, Any]]] = []

    progress_snapshot = JobProgress(phase="running", completed=1, total=2, elapsed_s=0.5)
    t = _spin_eci(eci_channel, _completed_record, progress=progress_snapshot)

    client = BlobProbeClient(
        control_channel,
        poll_interval_s=0.01,
        timeout_s=10.0,
        sleep=time.sleep,
        now=time.monotonic,
    )

    bundle = client.run_job(spec, on_progress=lambda p, m: progress_calls.append((p, m)))

    t.join(timeout=5.0)

    assert bundle.observations == {"ttft_ms": 120.0}
    assert bundle.series == {}
    assert bundle.artifacts == []

    # on_progress must have fired at least once for the progress snapshot.
    assert len(progress_calls) >= 1
    first_prog, first_metrics = progress_calls[0]
    assert first_prog.phase == "running"
    assert first_metrics == {"latency_ms": 55.0}


# ---------------------------------------------------------------------------
# (b) Failed job → ProbeJobFailed with record.error
# ---------------------------------------------------------------------------


def test_failed_job_raises_probe_job_failed_with_error(
    control_channel: BlobChannel,
    eci_channel: BlobChannel,
    spec: JobSpec,
) -> None:
    """run_job raises ProbeJobFailed carrying record.error when status == 'failed'."""
    error_msg = "probe crashed: connection refused"
    t = _spin_eci(eci_channel, lambda jid: _failed_record(jid, error_msg))

    client = BlobProbeClient(
        control_channel,
        poll_interval_s=0.01,
        timeout_s=10.0,
        sleep=time.sleep,
        now=time.monotonic,
    )

    with pytest.raises(ProbeJobFailed, match="connection refused"):
        client.run_job(spec)

    t.join(timeout=5.0)


# ---------------------------------------------------------------------------
# (c) Timeout: no result → ProbeJobFailed timeout message (injected clock)
# ---------------------------------------------------------------------------


def test_timeout_raises_probe_job_failed_with_timeout_message(
    control_channel: BlobChannel,
    spec: JobSpec,
) -> None:
    """run_job raises ProbeJobFailed with timeout message when deadline passes with no result."""
    # Injected clock that jumps past the deadline on the second call.
    calls = [0]

    def fake_now() -> float:
        calls[0] += 1
        # First call seeds the deadline (start time), second call is past deadline.
        return 0.0 if calls[0] == 1 else 9999.0

    client = BlobProbeClient(
        control_channel,
        poll_interval_s=0.0,
        timeout_s=300.0,
        sleep=lambda s: None,
        now=fake_now,
    )

    with pytest.raises(ProbeJobFailed, match="did not finish within 300"):
        client.run_job(spec)


# ---------------------------------------------------------------------------
# (d) Progress dedup: identical snapshot does not double-invoke on_progress
# ---------------------------------------------------------------------------


def test_progress_dedup_does_not_double_fire_for_same_snapshot(
    control_channel: BlobChannel,
    eci_channel: BlobChannel,
    spec: JobSpec,
) -> None:
    """Identical progress snapshot is reported only once even when polled multiple times."""
    progress_calls: list[JobProgress] = []

    # ECI writes the same progress then immediately the result.
    progress_snapshot = JobProgress(phase="running", completed=1, total=5, elapsed_s=1.0)

    def eci_make_record(job_id: str) -> JobRecord:
        # Write the same progress snapshot twice before writing the result.
        eci_channel.write_progress(job_id, progress_snapshot, {})
        eci_channel.write_progress(job_id, progress_snapshot, {})  # identical, overwrite
        return _completed_record(job_id)

    t = _spin_eci(eci_channel, eci_make_record)

    client = BlobProbeClient(
        control_channel,
        poll_interval_s=0.01,
        timeout_s=10.0,
        sleep=time.sleep,
        now=time.monotonic,
    )

    client.run_job(spec, on_progress=lambda p, _m: progress_calls.append(p))
    t.join(timeout=5.0)

    # The same snapshot must not fire on_progress more than once.
    assert len(progress_calls) == 1
    assert progress_calls[0].phase == "running"
