"""Minimal OSS client surface for channel ② (bulk telemetry).

Two implementations: Oss2Client (real, lazy oss2, default credential chain) and
InMemoryOssClient (dict-backed fake — the test double and the --probe=local
backend). The 4-method interface is all the sink/sync need; keeping it tiny
means the whole channel is testable without an account.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class OssClient(ABC):
    @abstractmethod
    def put_object(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get_object(self, key: str) -> bytes: ...

    @abstractmethod
    def list_prefix(self, prefix: str) -> list[str]: ...

    @abstractmethod
    def delete_object(self, key: str) -> None: ...


class InMemoryOssClient(OssClient):
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


class _ChainCredentialsProvider:
    """Bridges the ``alibabacloud_credentials`` default chain into oss2.

    Duck-typed to oss2's ``CredentialsProvider`` (just ``get_credentials()``), so
    OSS access uses the SAME identity source as the AgentRun client -- env / OIDC
    / CLI profile / instance RAM role, including STS security tokens -- instead of
    only static env-var AccessKeys. Kept import-light (no module-level oss2) so
    the package imports without the optional SDK."""

    def __init__(self, cred_client: object) -> None:
        self._cred_client = cred_client

    def get_credentials(self):  # noqa: ANN201 - lazy oss2 type
        import oss2

        c = self._cred_client.get_credential()
        return oss2.credentials.Credentials(
            c.access_key_id, c.access_key_secret, c.security_token
        )


class Oss2Client(OssClient):
    """Real OSS client using the alibabacloud default credential chain."""

    def __init__(self, bucket: str, region: str = "cn-hangzhou", endpoint: str = "") -> None:
        self._bucket_name = bucket
        self._region = region
        self._endpoint = endpoint or f"https://oss-{region}.aliyuncs.com"
        self._bucket: object | None = None

    def _bucket_handle(self):  # noqa: ANN202 - lazy oss2 type
        if self._bucket is None:
            import oss2
            from alibabacloud_credentials.client import Client as CredClient

            auth = oss2.ProviderAuthV4(_ChainCredentialsProvider(CredClient()))
            self._bucket = oss2.Bucket(auth, self._endpoint, self._bucket_name,
                                       region=self._region)
        return self._bucket

    def put_object(self, key: str, data: bytes) -> None:
        self._bucket_handle().put_object(key, data)

    def get_object(self, key: str) -> bytes:
        return self._bucket_handle().get_object(key).read()

    def list_prefix(self, prefix: str) -> list[str]:
        import oss2
        return [o.key for o in oss2.ObjectIterator(self._bucket_handle(), prefix=prefix)]

    def delete_object(self, key: str) -> None:
        self._bucket_handle().delete_object(key)
