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
        return oss2.credentials.Credentials(c.access_key_id, c.access_key_secret, c.security_token)


def _oss_endpoint(region: str, internal: bool) -> str:
    """Return the public or VPC-internal OSS endpoint for *region*."""
    if internal:
        return f"https://oss-{region}-internal.aliyuncs.com"
    return f"https://oss-{region}.aliyuncs.com"


class _Oss2BucketMixin(OssClient):
    """Shared oss2 Bucket CRUD; subclasses set ``_endpoint``, ``_bucket_name``,
    ``_region``, and override ``_bucket_handle()`` to inject auth."""

    _bucket_name: str
    _region: str
    _endpoint: str
    _bucket: object | None

    def _bucket_handle(self):  # noqa: ANN202 - lazy oss2 type  # pragma: no cover
        raise NotImplementedError

    def put_object(self, key: str, data: bytes) -> None:
        self._bucket_handle().put_object(key, data)

    def get_object(self, key: str) -> bytes:
        return self._bucket_handle().get_object(key).read()

    def list_prefix(self, prefix: str) -> list[str]:
        import oss2

        return [o.key for o in oss2.ObjectIterator(self._bucket_handle(), prefix=prefix)]

    def delete_object(self, key: str) -> None:
        self._bucket_handle().delete_object(key)

    def sign_url(self, key: str, expires: int = 3600, method: str = "GET") -> str:
        """Return a presigned URL for *key* (valid for up to *expires* seconds).

        The URL host is this client's endpoint, so signing on an ``internal=True``
        client yields a VPC-internal URL an in-region ECS instance can fetch. This
        is a local HMAC computation — no network call, so it works even when the
        internal endpoint is unreachable from where the control plane runs.

        With a STATIC AK the URL is valid for the full *expires* window; with a
        TEMPORARY credential (STS/instance role) the V4 signature also carries the
        security token, so validity is additionally capped by that token's expiry.
        """
        return str(self._bucket_handle().sign_url(method, key, expires, slash_safe=True))


class Oss2Client(_Oss2BucketMixin):
    """Real OSS client using the alibabacloud default credential chain.

    By default uses the **public** OSS endpoint. Pass ``internal=True`` to use
    the VPC-internal endpoint (``oss-<region>-internal.aliyuncs.com``); an
    explicit *endpoint* argument always takes precedence over both.
    """

    def __init__(
        self,
        bucket: str,
        region: str = "cn-hangzhou",
        endpoint: str = "",
        *,
        internal: bool = False,
    ) -> None:
        self._bucket_name = bucket
        self._region = region
        self._endpoint = endpoint or _oss_endpoint(region, internal)
        self._bucket: object | None = None

    def _bucket_handle(self):  # noqa: ANN202 - lazy oss2 type
        if self._bucket is None:
            import oss2
            from alibabacloud_credentials.client import Client as CredClient

            auth = oss2.ProviderAuthV4(_ChainCredentialsProvider(CredClient()))
            self._bucket = oss2.Bucket(auth, self._endpoint, self._bucket_name, region=self._region)
        return self._bucket


class EcsRamRoleOssClient(_Oss2BucketMixin):
    """OSS client for use inside an ECI/ECS instance.

    Authenticates via the instance's RAM role (``EcsRamRoleCredentialsProvider``)
    and always uses the **VPC-internal** OSS endpoint — no static keys, no public
    internet egress required.  Intended for the in-region probe only; the control
    plane keeps using :class:`Oss2Client`.
    """

    def __init__(self, bucket: str, region: str = "cn-hangzhou") -> None:
        self._bucket_name = bucket
        self._region = region
        self._endpoint = _oss_endpoint(region, internal=True)
        self._bucket: object | None = None

    def _bucket_handle(self):  # noqa: ANN202 - lazy oss2 type
        if self._bucket is None:
            import oss2

            auth = oss2.ProviderAuthV4(oss2.credentials.EcsRamRoleCredentialsProvider())
            self._bucket = oss2.Bucket(auth, self._endpoint, self._bucket_name, region=self._region)
        return self._bucket
