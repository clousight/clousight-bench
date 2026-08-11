"""Roll bulk telemetry to OSS as numbered JSONL chunks during a probe run.

The hot path appends records to per-stream buffers; a buffer flushes to a
numbered OSS object when it fills or on flush()/close(). Because chunks land in
OSS as they roll, the prefix is queryable mid-run (spec §6.2). close() writes a
manifest listing every chunk. No local disk needed for the account-free path —
the OssClient owns storage.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from .oss_client import OssClient

CHUNK_MEDIA = "application/x-ndjson"


class OssChunkSink:
    def __init__(self, client: OssClient, prefix: str, chunk_max_records: int = 1000) -> None:
        self._client = client
        self._prefix = prefix if (prefix == "" or prefix.endswith("/")) else prefix + "/"
        self._max = max(1, int(chunk_max_records))
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self._counters: dict[str, int] = {}
        self._manifest_chunks: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def append(self, stream: str, record: dict[str, Any]) -> None:
        with self._lock:
            buf = self._buffers.setdefault(stream, [])
            buf.append(record)
            if len(buf) >= self._max:
                self._flush_stream(stream)

    def flush(self, stream: str | None = None) -> None:
        with self._lock:
            streams = [stream] if stream is not None else list(self._buffers)
            for s in streams:
                if self._buffers.get(s):
                    self._flush_stream(s)

    def _flush_stream(self, stream: str) -> None:
        buf = self._buffers.get(stream) or []
        if not buf:
            return
        idx = self._counters.get(stream, 0)
        key = f"{self._prefix}{stream}-{idx:04d}.jsonl"
        data = ("\n".join(json.dumps(r, ensure_ascii=False) for r in buf) + "\n").encode("utf-8")
        self._client.put_object(key, data)
        self._manifest_chunks.append({
            "stream": stream,
            "key": key,
            "records": len(buf),
            "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            "media": CHUNK_MEDIA,
        })
        self._counters[stream] = idx + 1
        self._buffers[stream] = []

    def close(self) -> dict[str, Any]:
        self.flush()
        manifest = {"prefix": self._prefix, "chunks": list(self._manifest_chunks)}
        body = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        self._client.put_object(f"{self._prefix}manifest.json", body)
        return manifest
