"""Tests for S3Client and Ec2MetadataS3Client — account-free.

A _FakeS3Client is injected instead of real boto3; it backs put/get/list/delete
with a plain dict and raises a duck-typed ClientError (mimicking the botocore
shape) for missing keys.  No boto3 or botocore package is needed to run these
tests.

Mirror style: _FakeUpload / _FakeSigner in test_dev_wheel.py;
InMemoryOssClient usage in test_probe_oss_channel.py.
"""

from __future__ import annotations

import pytest

from clousight_bench.domains.agent_runtime.probe.s3_client import (
    Ec2MetadataS3Client,
    S3Client,
)

# ---------------------------------------------------------------------------
# Minimal botocore-style ClientError, no botocore import required
# ---------------------------------------------------------------------------


class _FakeClientError(Exception):
    """Duck-typed botocore ClientError: has .response dict with Error.Code."""

    def __init__(self, code: str, key: str = "") -> None:
        super().__init__(f"An error occurred ({code}) when calling the operation: {key}")
        self.response = {"Error": {"Code": code, "Message": key}}


# ---------------------------------------------------------------------------
# Fake S3 client
# ---------------------------------------------------------------------------


class _FakeS3Paginator:
    """Minimal paginator returned by get_paginator("list_objects_v2")."""

    def __init__(self, store: dict[str, bytes], bucket: str) -> None:
        self._store = store
        self._bucket = bucket

    def paginate(self, Bucket: str, Prefix: str = "") -> list[dict]:  # noqa: N803
        contents = [{"Key": k} for k in self._store if k.startswith(Prefix)]
        return [{"Contents": contents}] if contents else [{}]


class _FakeS3Client:
    """Dict-backed fake boto3 S3 client.

    Surfaces the subset of the boto3 S3 API used by S3Client:
    put_object, get_object, list_objects_v2 (via paginator), delete_object,
    generate_presigned_url.
    """

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:  # noqa: N803
        self._store[Key] = bytes(Body)

    def get_object(self, *, Bucket: str, Key: str) -> dict:  # noqa: N803
        if Key not in self._store:
            raise _FakeClientError("NoSuchKey", Key)

        class _Body:
            def __init__(self, data: bytes) -> None:
                self._data = data

            def read(self) -> bytes:
                return self._data

        return {"Body": _Body(self._store[Key])}

    def get_paginator(self, operation_name: str) -> _FakeS3Paginator:
        assert operation_name == "list_objects_v2"
        return _FakeS3Paginator(self._store, bucket="")

    def delete_object(self, *, Bucket: str, Key: str) -> None:  # noqa: N803
        self._store.pop(Key, None)

    def generate_presigned_url(
        self,
        operation: str,
        *,
        Params: dict,  # noqa: N803
        ExpiresIn: int,  # noqa: N803
    ) -> str:
        key = Params.get("Key", "")
        bucket = Params.get("Bucket", "")
        return f"https://{bucket}.s3.amazonaws.com/{key}?X-Amz-Expires={ExpiresIn}&op={operation}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client(fake: _FakeS3Client | None = None) -> S3Client:
    """Return an S3Client with a fake (or fresh fake) boto3 client injected."""
    return S3Client("test-bucket", "us-east-1", client=fake or _FakeS3Client())


# ---------------------------------------------------------------------------
# put / get round-trip
# ---------------------------------------------------------------------------


def test_put_get_round_trip() -> None:
    c = _client()
    c.put_object("a/b/c", b"hello world")
    assert c.get_object("a/b/c") == b"hello world"


def test_put_overwrites_existing_key() -> None:
    c = _client()
    c.put_object("k", b"first")
    c.put_object("k", b"second")
    assert c.get_object("k") == b"second"


# ---------------------------------------------------------------------------
# get missing key → KeyError
# ---------------------------------------------------------------------------


def test_get_missing_key_raises_key_error() -> None:
    c = _client()
    with pytest.raises(KeyError, match="missing-key"):
        c.get_object("missing-key")


def test_get_missing_key_wraps_original_exception() -> None:
    """KeyError.__cause__ should be the original ClientError-shaped exception."""
    c = _client()
    with pytest.raises(KeyError) as exc_info:
        c.get_object("no-such-key")
    assert exc_info.value.__cause__ is not None


def test_get_non_nosuchkey_error_propagates() -> None:
    """A ClientError with a different code must NOT be swallowed."""

    class _AccessDeniedFake(_FakeS3Client):
        def get_object(self, *, Bucket: str, Key: str) -> dict:  # noqa: N803
            raise _FakeClientError("AccessDenied", Key)

    c = S3Client("b", client=_AccessDeniedFake())
    with pytest.raises(_FakeClientError):
        c.get_object("some-key")


# ---------------------------------------------------------------------------
# list_prefix
# ---------------------------------------------------------------------------


def test_list_prefix_returns_matching_keys() -> None:
    c = _client()
    c.put_object("prefix/a", b"1")
    c.put_object("prefix/b", b"2")
    c.put_object("other/c", b"3")
    keys = c.list_prefix("prefix/")
    assert sorted(keys) == ["prefix/a", "prefix/b"]


def test_list_prefix_empty_when_no_match() -> None:
    c = _client()
    c.put_object("foo/x", b"data")
    assert c.list_prefix("bar/") == []


def test_list_prefix_exact_match_included() -> None:
    c = _client()
    c.put_object("exact", b"x")
    c.put_object("exactmore", b"y")
    # both start with "exact"
    keys = c.list_prefix("exact")
    assert "exact" in keys
    assert "exactmore" in keys


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_removes_key() -> None:
    c = _client()
    c.put_object("del-me", b"data")
    c.delete_object("del-me")
    with pytest.raises(KeyError):
        c.get_object("del-me")


def test_delete_nonexistent_key_is_idempotent() -> None:
    c = _client()
    c.delete_object("does-not-exist")  # must not raise


# ---------------------------------------------------------------------------
# sign_url
# ---------------------------------------------------------------------------


def test_sign_url_returns_string_containing_key() -> None:
    c = _client()
    url = c.sign_url("path/to/file.whl")
    assert "path/to/file.whl" in url
    assert url.startswith("https://")


def test_sign_url_passes_expires() -> None:
    c = _client()
    url = c.sign_url("k", expires=120)
    assert "120" in url


def test_sign_url_default_expires_3600() -> None:
    c = _client()
    url = c.sign_url("k")
    assert "3600" in url


def test_sign_url_get_uses_get_object_operation() -> None:
    c = _client()
    url = c.sign_url("k", method="GET")
    assert "get_object" in url


def test_sign_url_put_uses_put_object_operation() -> None:
    c = _client()
    url = c.sign_url("k", method="PUT")
    assert "put_object" in url


# ---------------------------------------------------------------------------
# Ec2MetadataS3Client
# ---------------------------------------------------------------------------


def test_ec2_client_is_subclass_of_s3_client() -> None:
    assert issubclass(Ec2MetadataS3Client, S3Client)


def test_ec2_client_accepts_injectable_client() -> None:
    """Ec2MetadataS3Client should accept an injected client for test isolation."""
    fake = _FakeS3Client()
    # Ec2MetadataS3Client doesn't expose client= kwarg directly in __init__,
    # but we can set it on the instance after construction to verify the
    # underlying mechanics work (same as S3Client lazy-init path).
    ec2_c = Ec2MetadataS3Client("my-bucket", "us-west-2")
    ec2_c._client = fake  # inject fake for account-free test
    ec2_c.put_object("probe/ready.json", b'{"ready": true}')
    assert ec2_c.get_object("probe/ready.json") == b'{"ready": true}'


def test_ec2_client_default_region_us_east_1() -> None:
    ec2_c = Ec2MetadataS3Client("b")
    assert ec2_c._region == "us-east-1"


def test_ec2_client_custom_region() -> None:
    ec2_c = Ec2MetadataS3Client("b", "eu-west-1")
    assert ec2_c._region == "eu-west-1"


def test_ec2_client_lazy_client_none_before_first_use() -> None:
    """Ec2MetadataS3Client does not eagerly create a boto3 client."""
    ec2_c = Ec2MetadataS3Client("b")
    assert ec2_c._client is None


# ---------------------------------------------------------------------------
# OssClient ABC compliance
# ---------------------------------------------------------------------------


def test_s3_client_implements_oss_client_abc() -> None:
    from clousight_bench.domains.agent_runtime.probe.oss_client import OssClient

    assert isinstance(_client(), OssClient)


def test_ec2_metadata_s3_client_implements_oss_client_abc() -> None:
    from clousight_bench.domains.agent_runtime.probe.oss_client import OssClient

    ec2_c = Ec2MetadataS3Client("b")
    assert isinstance(ec2_c, OssClient)
