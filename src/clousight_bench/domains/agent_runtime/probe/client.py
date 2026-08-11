"""csbench-side client for the cb-probe: dispatch a job, poll it to completion.

Async by design — POST /run-job returns a job_id, then short GET /job/<id>
polls carry live progress and, at the terminal poll, the full ObservationBundle.
No long-lived/streaming connection over the remote path (spec §5).
"""

from __future__ import annotations

import time
from collections.abc import Callable

import requests

from clousight_bench.core.observation import ObservationBundle

from .jobs import (
    TERMINAL_STATUSES,
    JobProgress,
    JobSpec,
    observation_bundle_from_dict,
)


class ProbeJobFailed(RuntimeError):
    """The remote job ended in failure, or polling exceeded the timeout."""


class RemoteProbeClient:
    def __init__(
        self, base_url: str, poll_interval_s: float = 2.0, timeout_s: float = 300.0, token: str | None = None
    ) -> None:
        self._base = base_url.rstrip("/")
        self._poll = poll_interval_s
        self._timeout = timeout_s
        self._http = requests.Session()
        if token:
            self._http.headers["Authorization"] = f"Bearer {token}"

    def run_job(
        self,
        spec: JobSpec,
        on_progress: Callable[[JobProgress, dict], None] | None = None,
    ) -> ObservationBundle:
        r = self._http.post(f"{self._base}/run-job", json=spec.to_dict(), timeout=30)
        r.raise_for_status()
        job_id = r.json()["job_id"]
        deadline = time.time() + self._timeout
        while time.time() < deadline:
            rec = self._http.get(f"{self._base}/job/{job_id}", timeout=30).json()
            if on_progress is not None:
                p = rec.get("progress") or {}
                on_progress(
                    JobProgress(
                        phase=str(p.get("phase", "")),
                        completed=int(p.get("completed", 0)),
                        total=int(p.get("total", 0)),
                        elapsed_s=float(p.get("elapsed_s", 0.0)),
                    ),
                    dict(rec.get("live_metrics") or {}),
                )
            status = rec.get("status")
            if status == "completed":
                return observation_bundle_from_dict(rec["observations"])
            if status in TERMINAL_STATUSES:
                raise ProbeJobFailed(rec.get("error") or f"probe job ended: {status}")
            time.sleep(self._poll)
        raise ProbeJobFailed(f"job {job_id} did not finish within {self._timeout}s")
