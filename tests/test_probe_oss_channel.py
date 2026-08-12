"""Tests for OssChannel — the OSS-mediated wire protocol between control plane
and in-region ECI probe.  All tests use InMemoryOssClient; no network, no account."""

from __future__ import annotations

import json

import pytest

from clousight_bench.domains.agent_runtime.probe.jobs import (
    JobProgress,
    JobRecord,
    JobSpec,
)
from clousight_bench.domains.agent_runtime.probe.oss_channel import OssChannel
from clousight_bench.domains.agent_runtime.probe.oss_client import InMemoryOssClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def oss() -> InMemoryOssClient:
    return InMemoryOssClient()


@pytest.fixture()
def channel(oss: InMemoryOssClient) -> OssChannel:
    return OssChannel(oss, campaign_id="camp-abc")


@pytest.fixture()
def spec() -> JobSpec:
    return JobSpec(
        probe="ttft",
        params={"samples": 3},
        target_endpoint="https://api.example.com/ep",
    )


# ---------------------------------------------------------------------------
# Key layout
# ---------------------------------------------------------------------------


def test_channel_prefix_is_stable(channel: OssChannel) -> None:
    assert channel.prefix == "clousight-bench/control/camp-abc/"


# ---------------------------------------------------------------------------
# write_job / read_job / list_pending_jobs
# ---------------------------------------------------------------------------


def test_write_job_returns_job_id_and_spec_roundtrips(
    channel: OssChannel, oss: InMemoryOssClient, spec: JobSpec
) -> None:
    job_id = channel.write_job(spec)
    assert job_id.startswith("job-")
    recovered = channel.read_job(job_id)
    assert recovered == spec


def test_write_job_stores_under_correct_key(
    channel: OssChannel, oss: InMemoryOssClient, spec: JobSpec
) -> None:
    job_id = channel.write_job(spec)
    key = f"clousight-bench/control/camp-abc/jobs/{job_id}.json"
    raw = json.loads(oss.get_object(key))
    assert raw["probe"] == "ttft"
    assert raw["params"] == {"samples": 3}


def test_list_pending_jobs_includes_written_job(channel: OssChannel, spec: JobSpec) -> None:
    job_id = channel.write_job(spec)
    assert job_id in channel.list_pending_jobs()


def test_list_pending_excludes_claimed_job(channel: OssChannel, spec: JobSpec) -> None:
    job_id = channel.write_job(spec)
    channel.claim(job_id)
    assert job_id not in channel.list_pending_jobs()


def test_list_pending_excludes_finished_job(channel: OssChannel, spec: JobSpec) -> None:
    job_id = channel.write_job(spec)
    rec = JobRecord(job_id=job_id, status="completed")
    channel.write_result(job_id, rec)
    assert job_id not in channel.list_pending_jobs()


def test_list_pending_multiple_jobs_mixed_state(channel: OssChannel, spec: JobSpec) -> None:
    j1 = channel.write_job(spec)
    j2 = channel.write_job(spec)
    j3 = channel.write_job(spec)
    channel.claim(j2)
    rec = JobRecord(job_id=j3, status="failed")
    channel.write_result(j3, rec)
    pending = channel.list_pending_jobs()
    assert j1 in pending
    assert j2 not in pending
    assert j3 not in pending


def test_read_job_raises_key_error_when_missing(channel: OssChannel) -> None:
    with pytest.raises(KeyError):
        channel.read_job("job-doesnotexist")


# ---------------------------------------------------------------------------
# write_progress / read_progress
# ---------------------------------------------------------------------------


def test_write_read_progress_roundtrip(channel: OssChannel, spec: JobSpec) -> None:
    job_id = channel.write_job(spec)
    prog = JobProgress(phase="sampling", completed=2, total=5, elapsed_s=1.5)
    metrics = {"latency_ms": 120.0}
    channel.write_progress(job_id, prog, metrics)
    result = channel.read_progress(job_id)
    assert result is not None
    got_prog, got_metrics = result
    assert got_prog.phase == "sampling"
    assert got_prog.completed == 2
    assert got_prog.total == 5
    assert got_prog.elapsed_s == pytest.approx(1.5)
    assert got_metrics == {"latency_ms": 120.0}


def test_read_progress_returns_none_when_missing(channel: OssChannel, spec: JobSpec) -> None:
    job_id = channel.write_job(spec)
    assert channel.read_progress(job_id) is None


def test_write_progress_overwrites_previous(channel: OssChannel, spec: JobSpec) -> None:
    job_id = channel.write_job(spec)
    channel.write_progress(job_id, JobProgress(phase="sampling", completed=1, total=5, elapsed_s=0.5), {})
    channel.write_progress(job_id, JobProgress(phase="sampling", completed=3, total=5, elapsed_s=1.5), {})
    prog, _ = channel.read_progress(job_id)  # type: ignore[misc]
    assert prog.completed == 3


# ---------------------------------------------------------------------------
# write_result / read_result
# ---------------------------------------------------------------------------


def test_write_read_result_roundtrip(channel: OssChannel, spec: JobSpec) -> None:
    job_id = channel.write_job(spec)
    rec = JobRecord(
        job_id=job_id,
        status="completed",
        progress=JobProgress(phase="done", completed=5, total=5, elapsed_s=3.0),
        live_metrics={"p50_ms": 200.0},
        chunk_refs=["chunk-0001", "chunk-0002"],
    )
    channel.write_result(job_id, rec)
    recovered = channel.read_result(job_id)
    assert recovered is not None
    assert recovered.job_id == job_id
    assert recovered.status == "completed"
    assert recovered.progress.phase == "done"
    assert recovered.live_metrics == {"p50_ms": 200.0}
    assert recovered.chunk_refs == ["chunk-0001", "chunk-0002"]


def test_read_result_returns_none_when_missing(channel: OssChannel, spec: JobSpec) -> None:
    job_id = channel.write_job(spec)
    assert channel.read_result(job_id) is None


def test_write_result_key_is_correct(channel: OssChannel, oss: InMemoryOssClient, spec: JobSpec) -> None:
    job_id = channel.write_job(spec)
    rec = JobRecord(job_id=job_id, status="failed", error="timeout")
    channel.write_result(job_id, rec)
    key = f"clousight-bench/control/camp-abc/jobs/{job_id}.result.json"
    raw = json.loads(oss.get_object(key))
    assert raw["status"] == "failed"
    assert raw["error"] == "timeout"


# ---------------------------------------------------------------------------
# ready marker
# ---------------------------------------------------------------------------


def test_is_ready_false_before_write(channel: OssChannel) -> None:
    assert channel.is_ready() is False


def test_write_ready_makes_is_ready_true(channel: OssChannel) -> None:
    channel.write_ready()
    assert channel.is_ready() is True


def test_write_ready_stores_json_under_correct_key(channel: OssChannel, oss: InMemoryOssClient) -> None:
    channel.write_ready()
    key = "clousight-bench/control/camp-abc/ready.json"
    raw = json.loads(oss.get_object(key))
    assert raw.get("ready") is True


# ---------------------------------------------------------------------------
# stop sentinel
# ---------------------------------------------------------------------------


def test_stop_not_requested_by_default(channel: OssChannel) -> None:
    assert channel.stop_requested() is False


def test_signal_stop_makes_stop_requested_true(channel: OssChannel) -> None:
    channel.signal_stop()
    assert channel.stop_requested() is True


def test_signal_stop_stores_under_correct_key(channel: OssChannel, oss: InMemoryOssClient) -> None:
    channel.signal_stop()
    key = "clousight-bench/control/camp-abc/stop"
    assert oss.get_object(key) == b""


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------


def test_claim_returns_true_first_call(channel: OssChannel, spec: JobSpec) -> None:
    job_id = channel.write_job(spec)
    assert channel.claim(job_id) is True


def test_claim_returns_false_second_call(channel: OssChannel, spec: JobSpec) -> None:
    job_id = channel.write_job(spec)
    assert channel.claim(job_id) is True
    assert channel.claim(job_id) is False


def test_claim_marker_stored_at_correct_key(
    channel: OssChannel, oss: InMemoryOssClient, spec: JobSpec
) -> None:
    job_id = channel.write_job(spec)
    channel.claim(job_id)
    key = f"clousight-bench/control/camp-abc/jobs/{job_id}.claimed"
    # key must exist (empty is fine)
    assert oss.get_object(key) == b""


# ---------------------------------------------------------------------------
# Two channels on the same OSS store (bi-directional)
# ---------------------------------------------------------------------------


def test_two_channels_same_oss_see_each_other(spec: JobSpec) -> None:
    """Simulate control-plane writer and ECI reader sharing same OSS bucket."""
    oss = InMemoryOssClient()
    control = OssChannel(oss, campaign_id="camp-bidir")
    probe = OssChannel(oss, campaign_id="camp-bidir")

    # Control writes job; probe sees it as pending
    job_id = control.write_job(spec)
    assert job_id in probe.list_pending_jobs()

    # Probe claims it
    assert probe.claim(job_id) is True
    assert job_id not in probe.list_pending_jobs()

    # Probe writes progress; control reads it
    probe.write_progress(job_id, JobProgress(phase="running", completed=1, total=3, elapsed_s=0.2), {})
    prog, _ = control.read_progress(job_id)  # type: ignore[misc]
    assert prog.phase == "running"

    # Probe signals ready; control can check
    probe.write_ready()
    assert control.is_ready() is True

    # Control signals stop; probe detects it
    control.signal_stop()
    assert probe.stop_requested() is True

    # Probe writes result; control reads it
    probe.write_result(job_id, JobRecord(job_id=job_id, status="completed"))
    rec = control.read_result(job_id)
    assert rec is not None and rec.status == "completed"


# ---------------------------------------------------------------------------
# Campaign isolation
# ---------------------------------------------------------------------------


def test_different_campaign_ids_are_isolated(spec: JobSpec) -> None:
    oss = InMemoryOssClient()
    ch_a = OssChannel(oss, campaign_id="camp-a")
    ch_b = OssChannel(oss, campaign_id="camp-b")

    ch_a.write_job(spec)
    ch_a.write_ready()
    ch_a.signal_stop()

    assert ch_b.list_pending_jobs() == []
    assert ch_b.is_ready() is False
    assert ch_b.stop_requested() is False
