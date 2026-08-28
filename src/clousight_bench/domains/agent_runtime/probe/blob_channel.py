"""Blob-store-mediated wire protocol between the local control plane and an
in-region probe.

Both sides import this module so that the key layout is the single source of
truth for the wire scheme.  No HTTP or TCP connection is required between them:
every message is a plain blob object under a per-campaign control prefix.

Key layout under ``clousight-bench/control/<campaign_id>/``:

    ready.json                  — readiness marker (probe → control)
    jobs/<job_id>.json          — job spec (control → probe)
    jobs/<job_id>.progress.json — latest progress snapshot (probe → control)
    jobs/<job_id>.result.json   — terminal JobRecord (probe → control)
    jobs/<job_id>.claimed       — best-effort claim marker (probe writes once)
    stop                        — stop sentinel (control → probe)
"""

from __future__ import annotations

import json
from typing import Any

from clousight_bench.core.blobstore import BlobStore
from clousight_bench.domains.agent_runtime.probe.jobs import (
    JobProgress,
    JobRecord,
    JobSpec,
    new_job_id,
)

_ENCODING = "utf-8"


def _dumps(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode(_ENCODING)


def _loads(data: bytes) -> Any:
    return json.loads(data.decode(_ENCODING))


class BlobChannel:
    """Bi-directional message channel over blob-store objects.

    Instantiate with the same *campaign_id* on both sides (control plane and
    in-region probe) and point both at the same ``BlobStore`` (or the same
    bucket via two separate provider clients, e.g. ``Oss2Client`` on Aliyun or
    ``S3Client`` on AWS).

    Args:
        store: The underlying blob store; use ``InMemoryBlobStore`` in tests.
        campaign_id: Unique identifier for the benchmark campaign.  Scopes all
            keys so that multiple campaigns sharing a bucket never interfere.
    """

    def __init__(self, store: BlobStore, campaign_id: str) -> None:
        self._store = store
        self._campaign_id = campaign_id
        self.prefix: str = f"clousight-bench/control/{campaign_id}/"

    # ------------------------------------------------------------------
    # Internal key helpers
    # ------------------------------------------------------------------

    def _key(self, *parts: str) -> str:
        return self.prefix + "/".join(parts)

    def _job_spec_key(self, job_id: str) -> str:
        return self._key("jobs", f"{job_id}.json")

    def _job_progress_key(self, job_id: str) -> str:
        return self._key("jobs", f"{job_id}.progress.json")

    def _job_result_key(self, job_id: str) -> str:
        return self._key("jobs", f"{job_id}.result.json")

    def _job_claimed_key(self, job_id: str) -> str:
        return self._key("jobs", f"{job_id}.claimed")

    def _ready_key(self) -> str:
        return self._key("ready.json")

    def _stop_key(self) -> str:
        return self._key("stop")

    # ------------------------------------------------------------------
    # Job dispatch (control → probe)
    # ------------------------------------------------------------------

    def write_job(self, spec: JobSpec) -> str:
        """Serialise *spec* to the blob store and return the newly-assigned job ID.

        The control plane calls this to queue a probe run.  The probe loop
        discovers it via :meth:`list_pending_jobs` and fetches it with
        :meth:`read_job`.
        """
        job_id = new_job_id()
        self._store.put_object(self._job_spec_key(job_id), _dumps(spec.to_dict()))
        return job_id

    def read_job(self, job_id: str) -> JobSpec:
        """Fetch and deserialise the JobSpec for *job_id*.

        Raises:
            KeyError: if no spec object exists for *job_id*.
        """
        data = self._store.get_object(self._job_spec_key(job_id))
        return JobSpec.from_dict(_loads(data))

    def list_pending_jobs(self) -> list[str]:
        """Return job IDs that are dispatched but not yet claimed or finished.

        A job is *pending* when it has a spec object (``.json``) but neither a
        result object (``.result.json``) nor a claim marker (``.claimed``).
        """
        jobs_prefix = self._key("jobs") + "/"
        all_keys = self._store.list_prefix(jobs_prefix)

        # Collect all known job IDs from spec keys (*.json but not *.progress.json
        # or *.result.json)
        spec_suffix = ".json"

        job_ids: list[str] = []
        result_set: set[str] = set()
        claimed_set: set[str] = set()

        for key in all_keys:
            filename = key[len(jobs_prefix) :]
            if filename.endswith(".result.json"):
                jid = filename[: -len(".result.json")]
                result_set.add(jid)
            elif filename.endswith(".claimed"):
                jid = filename[: -len(".claimed")]
                claimed_set.add(jid)
            elif filename.endswith(".progress.json"):
                pass  # not a spec key
            elif filename.endswith(spec_suffix):
                jid = filename[: -len(spec_suffix)]
                job_ids.append(jid)

        return [jid for jid in job_ids if jid not in result_set and jid not in claimed_set]

    # ------------------------------------------------------------------
    # Progress updates (probe → control)
    # ------------------------------------------------------------------

    def write_progress(self, job_id: str, progress: JobProgress, metrics: dict[str, Any]) -> None:
        """Overwrite the progress snapshot for *job_id*.

        Called repeatedly by the probe loop; the control plane polls with
        :meth:`read_progress`.  Overwrites rather than appending — only the
        latest snapshot matters.

        Args:
            job_id: Target job identifier.
            progress: Current :class:`JobProgress`.
            metrics: Free-form live metrics dict (e.g. ``{"latency_ms": 120.0}``).
        """
        payload = {
            "progress": progress.to_dict(),
            "metrics": metrics,
        }
        self._store.put_object(self._job_progress_key(job_id), _dumps(payload))

    def read_progress(self, job_id: str) -> tuple[JobProgress, dict[str, Any]] | None:
        """Return the latest ``(JobProgress, metrics)`` for *job_id*, or ``None``.

        Returns ``None`` when no progress object has been written yet.
        """
        try:
            data = self._store.get_object(self._job_progress_key(job_id))
        except KeyError:
            return None
        payload = _loads(data)
        pd = payload["progress"]
        prog = JobProgress(
            phase=str(pd.get("phase", "pending")),
            completed=int(pd.get("completed", 0)),
            total=int(pd.get("total", 0)),
            elapsed_s=float(pd.get("elapsed_s", 0.0)),
        )
        metrics: dict[str, Any] = payload.get("metrics") or {}
        return prog, metrics

    # ------------------------------------------------------------------
    # Terminal result (probe → control)
    # ------------------------------------------------------------------

    def write_result(self, job_id: str, record: JobRecord) -> None:
        """Persist the terminal *record* to the blob store.

        Once this object exists the job is considered finished and will not
        appear in :meth:`list_pending_jobs`.
        """
        self._store.put_object(self._job_result_key(job_id), _dumps(record.to_dict()))

    def read_result(self, job_id: str) -> JobRecord | None:
        """Return the terminal :class:`JobRecord` for *job_id*, or ``None``.

        Returns ``None`` when the job has not yet finished.
        """
        try:
            data = self._store.get_object(self._job_result_key(job_id))
        except KeyError:
            return None
        d = _loads(data)
        pd = d.get("progress") or {}
        progress = JobProgress(
            phase=str(pd.get("phase", "pending")),
            completed=int(pd.get("completed", 0)),
            total=int(pd.get("total", 0)),
            elapsed_s=float(pd.get("elapsed_s", 0.0)),
        )
        return JobRecord(
            job_id=str(d["job_id"]),
            status=str(d.get("status", "pending")),
            progress=progress,
            live_metrics=dict(d.get("live_metrics") or {}),
            observations=d.get("observations"),
            error=d.get("error"),
            chunk_refs=list(d.get("chunk_refs") or []),
        )

    # ------------------------------------------------------------------
    # Readiness marker (probe → control)
    # ------------------------------------------------------------------

    def write_ready(self) -> None:
        """Mark the probe loop as live and accepting jobs.

        Called once by the probe at startup, after its HTTP server (if any)
        is bound and its polling loop is running.
        """
        self._store.put_object(self._ready_key(), _dumps({"ready": True}))

    def is_ready(self) -> bool:
        """Return ``True`` if the probe loop has written its readiness marker."""
        try:
            self._store.get_object(self._ready_key())
            return True
        except KeyError:
            return False

    # ------------------------------------------------------------------
    # Stop sentinel (control → probe)
    # ------------------------------------------------------------------

    def signal_stop(self) -> None:
        """Write the stop sentinel; the probe loop will drain and exit.

        The sentinel is an empty object so that writing it is atomic and cheap.
        """
        self._store.put_object(self._stop_key(), b"")

    def stop_requested(self) -> bool:
        """Return ``True`` if the control plane has signalled a stop."""
        try:
            self._store.get_object(self._stop_key())
            return True
        except KeyError:
            return False

    def reset(self) -> None:
        """Delete every control key for this campaign (stop / ready / jobs).

        Campaign prefixes are reused when the target has no run_id (they fall back
        to ``adhoc``), so a previous run's ``stop`` sentinel — or a stale ready /
        job — would otherwise linger and make a freshly booted probe exit on its
        first poll. The control plane calls this before provisioning a carrier so
        each campaign starts from a clean prefix.
        """
        for key in self._store.list_prefix(self.prefix):
            self._store.delete_object(key)

    # ------------------------------------------------------------------
    # Best-effort claim (probe)
    # ------------------------------------------------------------------

    def claim(self, job_id: str) -> bool:
        """Attempt to claim *job_id* for exclusive processing.

        Because exactly one probe consumer runs per campaign, true CAS is not
        required.  This writes a best-effort marker object and returns whether
        *this call* created it.

        Returns:
            ``True`` if the claim was successful (first caller), ``False`` if
            the marker already existed (duplicate call or restart).
        """
        key = self._job_claimed_key(job_id)
        try:
            self._store.get_object(key)
            return False  # already claimed
        except KeyError:
            self._store.put_object(key, b"")
            return True
