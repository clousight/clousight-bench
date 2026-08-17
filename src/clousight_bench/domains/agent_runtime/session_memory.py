"""ObjectStoreSessionMemory: the session-state K/V shared by every cloud transport.

T1.2 (state persistence), T1.11 (concurrent writes) and T6.1 (isolation) each
store a session's state as one JSON object and read it back. A managed agent
runtime's own "memory" API is typically a RAG/vector store, not a plain K/V, so
we use the object store the adapter already has (OSS on Aliyun, S3 on AWS). The
key layout and the store/fetch/cleanup loop are identical across clouds — only
the blob backend differs — so they live here once, parameterised by any
``OssClient`` (Oss2Client for Aliyun, S3Client for AWS).
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from clousight_bench.domains.agent_runtime.probe.oss_client import OssClient


class ObjectStoreSessionMemory:
    """Session-state store over any ``OssClient`` blob backend.

    A session's state lives at ``clousight-bench/state/{run_id}/{session_id}.json``.
    ``fetch`` raises ``KeyError`` for an absent session (the ``OssClient``
    contract normalises the backend's not-found error). ``cleanup`` deletes only
    what this instance wrote — tracked in ``_keys`` — matching the teardown
    contract of the original per-cloud memory classes.
    """

    def __init__(self, blob: OssClient, run_id: str | None = None) -> None:
        self._blob = blob
        self._run_id = run_id or "default"
        self._keys: list[str] = []

    def _key(self, session_id: str) -> str:
        return f"clousight-bench/state/{self._run_id}/{session_id}.json"

    def store(self, session_id: str, state: dict[str, Any]) -> None:
        key = self._key(session_id)
        self._blob.put_object(key, json.dumps(state).encode("utf-8"))
        if key not in self._keys:
            self._keys.append(key)

    def fetch(self, session_id: str) -> dict[str, Any]:
        data = self._blob.get_object(self._key(session_id))
        return json.loads(data.decode("utf-8"))

    def cleanup(self) -> None:
        for key in list(self._keys):
            with contextlib.suppress(Exception):
                self._blob.delete_object(key)
        self._keys.clear()
