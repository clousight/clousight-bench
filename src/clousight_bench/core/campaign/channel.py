"""Campaign-level blob-store objects for the ecs prod profile.

Layers campaign-scoped objects (launch spec, progress manifest, heartbeat,
ledger snapshot, log stream, per-task results, terminal marker, stop sentinel)
onto the same per-campaign prefix used by :class:`BlobChannel`
(``clousight-bench/control/<campaign_id>/``). All comms is plain blob objects so
the laptop and the controller never hold a connection open.

Reads swallow the "object not found" ``KeyError`` and return ``None`` / falsy,
mirroring ``BlobChannel.is_ready``/``stop_requested``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from clousight_bench.core.blobstore import BlobStore
from clousight_bench.core.campaign.spec import CampaignManifest, LaunchSpec


class CampaignChannel:
    """Read/write the campaign control objects on the blob store."""

    def __init__(self, store: BlobStore, campaign_id: str, *, now: Callable[[], float] = time.time) -> None:
        self._store = store
        self.campaign_id = campaign_id
        self._now = now
        self.prefix = f"clousight-bench/control/{campaign_id}/"

    def _key(self, *parts: str) -> str:
        return self.prefix + "/".join(parts)

    def _get(self, key: str) -> bytes | None:
        try:
            return self._store.get_object(key)
        except KeyError:
            return None

    # --- launch spec (laptop → controller) --------------------------------
    def write_launch(self, spec: LaunchSpec) -> None:
        self._store.put_object(self._key("launch.json"), spec.to_json())

    def read_launch(self) -> LaunchSpec | None:
        data = self._get(self._key("launch.json"))
        return LaunchSpec.from_json(data) if data is not None else None

    # --- progress manifest (controller → laptop) --------------------------
    def write_manifest(self, manifest: CampaignManifest) -> None:
        self._store.put_object(self._key("status", "manifest.json"), manifest.to_json())

    def read_manifest(self) -> CampaignManifest | None:
        data = self._get(self._key("status", "manifest.json"))
        return CampaignManifest.from_json(data) if data is not None else None

    # --- heartbeat --------------------------------------------------------
    def write_heartbeat(self, current_task: str, phase: str) -> None:
        payload = {"ts": self._now(), "current_task": current_task, "phase": phase}
        self._store.put_object(self._key("heartbeat.json"), json.dumps(payload).encode("utf-8"))

    def read_heartbeat(self) -> dict | None:
        data = self._get(self._key("heartbeat.json"))
        return json.loads(data) if data is not None else None

    # --- ledger snapshot (controller → blob store, for leak-proof teardown) ---
    def write_ledger(self, raw: bytes) -> None:
        self._store.put_object(self._key("ledger.json"), raw)

    def read_ledger(self) -> bytes | None:
        return self._get(self._key("ledger.json"))

    # --- log stream -------------------------------------------------------
    def _logs_prefix(self) -> str:
        return self._key("logs") + "/"

    def append_log(self, line: str) -> None:
        seq = len(self._store.list_prefix(self._logs_prefix()))
        self._store.put_object(self._logs_prefix() + f"{seq:08d}.log", line.encode("utf-8"))

    def read_logs(self) -> list[str]:
        keys = sorted(self._store.list_prefix(self._logs_prefix()))
        return [self._store.get_object(k).decode("utf-8") for k in keys]

    # --- per-task results (JSON + optional parquet sidecar) ---------------
    def _results_prefix(self) -> str:
        return self._key("results") + "/"

    def write_result(self, name: str, json_bytes: bytes, parquet_bytes: bytes | None) -> None:
        """``name`` is an opaque result key — the controller uses ``<task_id>--<run_id>``
        so repeated executions of the same task never overwrite each other."""
        self._store.put_object(self._results_prefix() + f"{name}.json", json_bytes)
        if parquet_bytes is not None:
            self._store.put_object(self._results_prefix() + f"{name}.series.parquet", parquet_bytes)

    def read_result(self, name: str) -> tuple[bytes, bytes | None]:
        j = self._store.get_object(self._results_prefix() + f"{name}.json")
        p = self._get(self._results_prefix() + f"{name}.series.parquet")
        return j, p

    def list_results(self) -> list[str]:
        rp = self._results_prefix()
        ids = [k[len(rp) : -len(".json")] for k in self._store.list_prefix(rp) if k.endswith(".json")]
        return sorted(ids)

    # --- terminal marker --------------------------------------------------
    def write_done(self, ok: bool) -> None:
        self._store.put_object(self._key("DONE" if ok else "FAILED"), b"")

    def is_done(self) -> str | None:
        if self._get(self._key("DONE")) is not None:
            return "DONE"
        if self._get(self._key("FAILED")) is not None:
            return "FAILED"
        return None

    # --- stop sentinel (laptop teardown → controller) --------------------
    def signal_stop(self) -> None:
        self._store.put_object(self._key("stop"), b"")

    def stop_requested(self) -> bool:
        return self._get(self._key("stop")) is not None

    # --- idempotent claim (controller startup) ---------------------------
    def claim(self) -> bool:
        key = self._key("claimed")
        if self._get(key) is not None:
            return False
        self._store.put_object(key, b"")
        return True
