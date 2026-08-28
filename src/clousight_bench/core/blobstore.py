"""Blob-store interface for campaign control/telemetry channels.

The 4-method surface (put/get/list/delete) is everything the campaign channel,
sink and sync need; keeping it tiny means the whole channel is testable without
a cloud account. Cloud implementations live with their providers
(``domains/agent_runtime/probe/oss_client.py`` for Aliyun OSS,
``.../probe/s3_client.py`` for AWS S3); ``InMemoryBlobStore`` is the dict-backed
test double and the local no-account backend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BlobStore(ABC):
    @abstractmethod
    def put_object(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get_object(self, key: str) -> bytes: ...

    @abstractmethod
    def list_prefix(self, prefix: str) -> list[str]: ...

    @abstractmethod
    def delete_object(self, key: str) -> None: ...


class InMemoryBlobStore(BlobStore):
    """Dict-backed fake; also the local (no-account) backend."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def put_object(self, key: str, data: bytes) -> None:
        self._store[key] = bytes(data)

    def get_object(self, key: str) -> bytes:
        return self._store[key]  # KeyError on missing, by contract

    def list_prefix(self, prefix: str) -> list[str]:
        return sorted(k for k in self._store if k.startswith(prefix))

    def delete_object(self, key: str) -> None:
        self._store.pop(key, None)
