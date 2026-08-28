"""Control-plane blob-store dispatch client.

Drop-in replacement for :class:`RemoteProbeClient` that never opens an HTTP
connection to the in-region probe.  Every message travels through
:class:`BlobChannel`: the job spec is written as a blob object, progress is
polled from another blob object, and the terminal :class:`JobRecord` is read
back the same way.

The public ``run_job`` signature and exception type are identical to
:class:`RemoteProbeClient` so call-sites can swap the two without any changes.
"""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from typing import Any

from clousight_bench.core.observation import ObservationBundle

from .blob_channel import BlobChannel
from .client import ProbeJobFailed
from .jobs import (
    JobProgress,
    JobSpec,
    observation_bundle_from_dict,
)


class BlobProbeClient:
    """Dispatch probe jobs and collect results through an :class:`BlobChannel`.

    Args:
        channel: The shared blob-store channel; must be scoped to the correct campaign.
        poll_interval_s: Seconds to sleep between result-poll attempts.
        timeout_s: Maximum wall-clock seconds to wait for a terminal result.
        sleep: Injectable sleep function (default :func:`time.sleep`).
        now: Injectable monotonic clock (default :func:`time.monotonic`).
    """

    def __init__(
        self,
        channel: BlobChannel,
        *,
        poll_interval_s: float = 2.0,
        timeout_s: float = 300.0,
        sleep: Callable[[float], None] = _time.sleep,
        now: Callable[[], float] = _time.monotonic,
    ) -> None:
        self._channel = channel
        self._poll = poll_interval_s
        self._timeout = timeout_s
        self._sleep = sleep
        self._now = now

    def run_job(
        self,
        spec: JobSpec,
        on_progress: Callable[[JobProgress, dict[str, Any]], None] | None = None,
    ) -> ObservationBundle:
        """Write *spec* to the channel, poll until terminal, return the bundle.

        Raises:
            ProbeJobFailed: if the job ends with ``status == "failed"`` (carries
                ``record.error``) or if the deadline is exceeded before a
                terminal result appears.
        """
        job_id = self._channel.write_job(spec)
        deadline = self._now() + self._timeout
        last_progress_key: tuple[Any, ...] | None = None

        while self._now() < deadline:
            # Check progress before checking result so on_progress can fire on
            # the same poll that discovers the terminal state.
            if on_progress is not None:
                snap = self._channel.read_progress(job_id)
                if snap is not None:
                    progress, metrics = snap
                    progress_key = (
                        progress.phase,
                        progress.completed,
                        progress.total,
                        progress.elapsed_s,
                    )
                    if progress_key != last_progress_key:
                        on_progress(progress, metrics)
                        last_progress_key = progress_key

            record = self._channel.read_result(job_id)
            if record is None:
                self._sleep(self._poll)
                continue

            if record.status == "completed":
                obs_dict = record.observations or {}
                return observation_bundle_from_dict(obs_dict)

            # Any other terminal status (e.g. "failed").
            raise ProbeJobFailed(record.error or f"probe job ended: {record.status}")

        raise ProbeJobFailed(f"job {job_id} did not finish within {self._timeout}s")
