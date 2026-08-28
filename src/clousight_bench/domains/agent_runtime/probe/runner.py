"""Run probe jobs asynchronously and expose their live state.

submit() starts a daemon thread and returns immediately with a job_id; the
thread runs the named probe, streaming JobProgress into the shared JobRecord
under a lock. get() returns a thread-safe snapshot for the poll endpoint.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from clousight_bench.core.observation import ObservationBundle

from .jobs import JobProgress, JobRecord, JobSpec, new_job_id

if TYPE_CHECKING:
    from .blob_sink import BlobChunkSink

ProbeFn = Callable[..., ObservationBundle]
SinkFactory = Callable[[JobSpec], "BlobChunkSink | None"]


class JobRunner:
    def __init__(self, probes: dict[str, ProbeFn], sink_factory: SinkFactory | None = None) -> None:
        self._probes = dict(probes)
        self._sink_factory = sink_factory
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def submit(self, spec: JobSpec) -> str:
        if spec.probe not in self._probes:
            raise KeyError(f"unknown probe {spec.probe!r}")
        job_id = new_job_id()
        with self._lock:
            self._jobs[job_id] = JobRecord(job_id=job_id, status="running")
        threading.Thread(target=self._run, args=(job_id, spec), daemon=True).start()
        return job_id

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            rec = self._jobs.get(job_id)
            return copy.deepcopy(rec) if rec is not None else None

    def _run(self, job_id: str, spec: JobSpec) -> None:
        def progress_cb(prog: JobProgress, metrics: dict[str, Any]) -> None:
            with self._lock:
                rec = self._jobs[job_id]
                rec.progress = prog
                rec.live_metrics = dict(metrics)

        sink = self._sink_factory(spec) if self._sink_factory else None
        try:
            fn = self._probes[spec.probe]
            bundle = fn(spec, progress_cb, sink=sink) if sink is not None else fn(spec, progress_cb)
            with self._lock:
                rec = self._jobs[job_id]
                rec.status = "completed"
                rec.observations = bundle.to_dict()
        except Exception as exc:  # noqa: BLE001 — surface any probe failure as job failure
            with self._lock:
                rec = self._jobs[job_id]
                rec.status = "failed"
                rec.error = f"{type(exc).__name__}: {exc}"
        finally:
            if sink is not None:
                try:
                    manifest = sink.close()
                    with self._lock:
                        # We lift only chunk keys here; the full manifest→artifacts
                        # promotion is the deliberate seam blob_sync.chunks_to_artifacts,
                        # intended for control-plane result assembly (not yet wired).
                        self._jobs[job_id].chunk_refs = [ch["key"] for ch in manifest.get("chunks", [])]
                except Exception:  # noqa: BLE001 — sink flush must not mask job result
                    pass
