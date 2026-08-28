"""Tests for the ECI-side poller loop (agent_loop.py).

All tests use InMemoryBlobStore + a real BlobChannel + a synchronous fake runner
so they are deterministic and instant — no real sleep, no real network.

The injected ``now`` and ``sleep`` callables let tests drive time without
waiting real wall-clock seconds.
"""

from __future__ import annotations

import time as real_time
from collections.abc import Callable
from typing import Any

from clousight_bench.core.blobstore import InMemoryBlobStore
from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime.probe.agent_loop import _run_job, run_agent_loop
from clousight_bench.domains.agent_runtime.probe.blob_channel import BlobChannel
from clousight_bench.domains.agent_runtime.probe.jobs import (
    JobProgress,
    JobRecord,
    JobSpec,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_channel(campaign_id: str = "camp-test") -> tuple[BlobChannel, InMemoryBlobStore]:
    store = InMemoryBlobStore()
    channel = BlobChannel(store, campaign_id=campaign_id)
    return channel, store


class _FakeRunner:
    """Minimal runner interface that drives the loop without threading.

    Probes run synchronously inside submit(), so there is no race between the
    loop's poll and the probe finishing.
    """

    def __init__(self, probe_fn: Callable | None = None) -> None:
        def _default(spec: JobSpec, progress_cb: Callable, **_kw: Any) -> ObservationBundle:
            progress_cb(
                JobProgress(phase="running", completed=1, total=1, elapsed_s=0.1),
                {"latency_ms": 42.0},
            )
            return ObservationBundle(observations={"ok": True}, series={})

        self._fn = probe_fn or _default
        self._jobs: dict[str, JobRecord] = {}

    def submit(self, spec: JobSpec) -> str:
        from clousight_bench.domains.agent_runtime.probe.jobs import new_job_id

        job_id = new_job_id()
        record = JobRecord(job_id=job_id, status="running")
        self._jobs[job_id] = record

        def _progress_cb(prog: JobProgress, metrics: dict[str, Any]) -> None:
            record.progress = prog
            record.live_metrics = dict(metrics)

        try:
            bundle = self._fn(spec, _progress_cb)
            record.status = "completed"
            record.observations = bundle.to_dict()
        except Exception as exc:  # noqa: BLE001
            record.status = "failed"
            record.error = f"{type(exc).__name__}: {exc}"

        return job_id

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)


class _MonotonicClock:
    """Controllable monotonic clock for injecting into run_agent_loop."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def advance(self, delta: float) -> None:
        self._t += delta

    def __call__(self) -> float:
        return self._t


def _noop_sleep(_s: float) -> None:
    """Drop-in sleep() replacement that returns immediately."""


# ---------------------------------------------------------------------------
# (a) loop writes ready marker on startup
# ---------------------------------------------------------------------------


def test_loop_writes_ready_on_startup() -> None:
    channel, _ = _make_channel()
    runner = _FakeRunner()
    clock = _MonotonicClock()

    # Stop the loop immediately: signal stop before it enters the poll loop.
    channel.signal_stop()

    assert not channel.is_ready()
    run_agent_loop(
        channel,
        runner,
        idle_timeout_s=1.0,
        poll_interval_s=0.0,
        sleep=_noop_sleep,
        now=clock,
    )
    assert channel.is_ready()


# ---------------------------------------------------------------------------
# (b) a queued job runs and its result lands via channel.read_result
# ---------------------------------------------------------------------------


def test_queued_job_runs_and_result_is_written() -> None:
    channel, _ = _make_channel()
    runner = _FakeRunner()
    clock = _MonotonicClock()

    spec = JobSpec(probe="fake", params={}, target_endpoint="https://api.example.com")
    job_id = channel.write_job(spec)

    # After one iteration the job should be processed; then we signal stop so
    # the loop exits cleanly on the next check.
    call_count = 0

    def _stop_after_one_job() -> bool:
        nonlocal call_count
        call_count += 1
        # First call: not stopped (let the loop process pending jobs).
        # Second call and beyond: stop.
        return call_count > 1

    channel.stop_requested = _stop_after_one_job  # type: ignore[method-assign]

    run_agent_loop(
        channel,
        runner,
        idle_timeout_s=1.0,
        poll_interval_s=0.0,
        sleep=_noop_sleep,
        now=clock,
    )

    result = channel.read_result(job_id)
    assert result is not None
    assert result.status == "completed"
    assert result.observations is not None and result.observations.get("observations") == {"ok": True}


# ---------------------------------------------------------------------------
# (c) loop exits on stop sentinel
# ---------------------------------------------------------------------------


def test_loop_exits_on_stop_sentinel() -> None:
    channel, _ = _make_channel()
    runner = _FakeRunner()
    clock = _MonotonicClock()

    channel.signal_stop()

    # Should return quickly without hanging.
    start = real_time.monotonic()
    run_agent_loop(
        channel,
        runner,
        idle_timeout_s=120.0,
        poll_interval_s=0.0,
        sleep=_noop_sleep,
        now=clock,
    )
    elapsed = real_time.monotonic() - start
    assert elapsed < 1.0, "loop should exit immediately on stop sentinel"


# ---------------------------------------------------------------------------
# (d) loop exits after idle_timeout with no jobs
# ---------------------------------------------------------------------------


def test_loop_exits_after_idle_timeout() -> None:
    channel, _ = _make_channel()
    runner = _FakeRunner()
    clock = _MonotonicClock(start=0.0)

    # After each sleep call we advance the clock beyond the idle timeout so
    # the loop exits after a single iteration.
    def _advancing_sleep(s: float) -> None:
        clock.advance(200.0)  # jump past idle_timeout_s=120.0

    run_agent_loop(
        channel,
        runner,
        idle_timeout_s=120.0,
        poll_interval_s=0.0,
        sleep=_advancing_sleep,
        now=clock,
    )
    # If we got here the loop exited — that's the assertion.


# ---------------------------------------------------------------------------
# (e) a probe that raises yields a failed JobRecord; loop keeps going / exits cleanly
# ---------------------------------------------------------------------------


def test_probe_exception_yields_failed_record_and_loop_continues() -> None:
    channel, _ = _make_channel()

    def _boom(spec: JobSpec, progress_cb: Callable, **_kw: Any) -> ObservationBundle:
        raise RuntimeError("intentional boom")

    runner = _FakeRunner(probe_fn=_boom)
    clock = _MonotonicClock()

    spec = JobSpec(probe="fake", params={}, target_endpoint="https://api.example.com")
    job_id = channel.write_job(spec)

    call_count = 0

    def _stop_after_one_job() -> bool:
        nonlocal call_count
        call_count += 1
        return call_count > 1

    channel.stop_requested = _stop_after_one_job  # type: ignore[method-assign]

    # Should not raise even though the probe raises.
    run_agent_loop(
        channel,
        runner,
        idle_timeout_s=1.0,
        poll_interval_s=0.0,
        sleep=_noop_sleep,
        now=clock,
    )

    result = channel.read_result(job_id)
    assert result is not None
    assert result.status == "failed"
    assert result.error is not None
    assert "boom" in result.error


# ---------------------------------------------------------------------------
# (f) a claimed job is not processed twice
# ---------------------------------------------------------------------------


def test_claimed_job_is_not_processed_twice() -> None:
    channel, _ = _make_channel()

    run_count = 0

    def _counting_probe(spec: JobSpec, progress_cb: Callable, **_kw: Any) -> ObservationBundle:
        nonlocal run_count
        run_count += 1
        return ObservationBundle(observations={}, series={})

    runner = _FakeRunner(probe_fn=_counting_probe)
    clock = _MonotonicClock()

    spec = JobSpec(probe="fake", params={}, target_endpoint="https://api.example.com")
    job_id = channel.write_job(spec)

    # Pre-claim the job so the loop sees it as already claimed.
    already_claimed = channel.claim(job_id)
    assert already_claimed  # first claim succeeds

    channel.signal_stop()

    run_agent_loop(
        channel,
        runner,
        idle_timeout_s=1.0,
        poll_interval_s=0.0,
        sleep=_noop_sleep,
        now=clock,
    )

    # Because the job was already claimed, the loop must NOT have run it again.
    assert run_count == 0


# ---------------------------------------------------------------------------
# Additional: progress is streamed while a job is in flight (using real threads)
# ---------------------------------------------------------------------------


def test_progress_is_written_during_job_execution() -> None:
    """Progress callback writes must appear in the channel before result lands."""
    channel, _ = _make_channel()

    def _slow_probe(spec: JobSpec, progress_cb: Callable, **_kw: Any) -> ObservationBundle:
        prog = JobProgress(phase="loading", completed=0, total=1, elapsed_s=0.0)
        progress_cb(prog, {"stage": "loading"})
        return ObservationBundle(observations={"done": True}, series={})

    runner = _FakeRunner(probe_fn=_slow_probe)
    clock = _MonotonicClock()

    spec = JobSpec(probe="fake", params={}, target_endpoint="https://api.example.com")
    job_id = channel.write_job(spec)

    call_count = 0

    def _stop_after_one_job() -> bool:
        nonlocal call_count
        call_count += 1
        return call_count > 1

    channel.stop_requested = _stop_after_one_job  # type: ignore[method-assign]

    run_agent_loop(
        channel,
        runner,
        idle_timeout_s=1.0,
        poll_interval_s=0.0,
        sleep=_noop_sleep,
        now=clock,
    )

    # Result must exist.
    result = channel.read_result(job_id)
    assert result is not None and result.status == "completed"

    # Progress must have been written.
    prog_result = channel.read_progress(job_id)
    assert prog_result is not None
    prog, metrics = prog_result
    assert prog.phase == "loading"
    assert metrics == {"stage": "loading"}


# ---------------------------------------------------------------------------
# I1: _run_job gives up after max_wait_s when runner never reaches terminal status
# ---------------------------------------------------------------------------


class _HangingRunner:
    """Runner whose get() always returns status='running' — simulates a stuck thread."""

    def submit(self, spec: JobSpec) -> str:
        return "stuck-job-id"

    def get(self, job_id: str) -> JobRecord:
        return JobRecord(job_id=job_id, status="running")


def test_run_job_times_out_when_runner_never_completes() -> None:
    """_run_job must give up after max_wait_s and write a failed record."""
    channel, _ = _make_channel()
    clock = _MonotonicClock(start=0.0)

    # Each sleep(0.01) call advances the clock past max_wait_s (10.0 s here) so the
    # timeout path is reached after exactly one poll iteration.  No real time passes.
    def _timeout_sleep(s: float) -> None:
        clock.advance(20.0)  # jump past max_wait_s=10.0

    spec = JobSpec(probe="fake", params={}, target_endpoint="https://api.example.com")
    job_id = "job-timeout-test"

    result = _run_job(
        channel,
        _HangingRunner(),
        job_id,
        spec,
        sleep=_timeout_sleep,
        now=clock,
        max_wait_s=10.0,
    )

    assert result.status == "failed"
    assert result.error is not None
    assert "10.0" in result.error  # max_wait_s value appears in the message
    assert "did not complete" in result.error
