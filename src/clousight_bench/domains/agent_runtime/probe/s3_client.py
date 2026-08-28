"""AWS S3 implementations of the core blob-store interface.

S3Client (control plane: boto3, injectable for testing, lazy import) and
Ec2MetadataS3Client (in-instance: EC2 instance-profile credential chain via
boto3's built-in IMDSv2 resolution).
The interface + in-memory fake live in ``clousight_bench.core.blobstore``.

The ``sign_url`` method is an extension beyond the BlobStore ABC (matching
``_Oss2BucketMixin.sign_url``) for dev-wheel presigning parity.
"""

from __future__ import annotations

from typing import Any

from clousight_bench.core.blobstore import BlobStore


def _get_error_code(exc: BaseException) -> str:
    """Extract the S3/botocore error code from a ClientError using duck-typing.

    Avoids a hard import of botocore.exceptions at module level — boto3 stays
    fully lazy.  Returns an empty string if the exception is not a ClientError-
    shaped object.
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return ""
    return str(response.get("Error", {}).get("Code", ""))


class S3Client(BlobStore):
    """Control-plane S3 client (boto3).

    Constructor: ``(bucket, region="us-east-1", *, client=None)``.

    *client* is the injection point: pass a fake/stub boto3 S3 client in tests
    to keep the suite account-free.  When *client* is None the real boto3 client
    is created lazily on first use (same lazy-import pattern as Oss2Client /
    Ecs20140526Sdk so the package can be imported without boto3 installed).

    All four BlobStore ABC methods are implemented plus ``sign_url`` for
    dev-wheel presigning parity with ``_Oss2BucketMixin``.
    """

    def __init__(
        self,
        bucket: str,
        region: str = "us-east-1",
        *,
        client: Any | None = None,
    ) -> None:
        self._bucket = bucket
        self._region = region
        self._client = client

    def _cli(self) -> Any:
        """Return the boto3 S3 client, creating it lazily on first use."""
        if self._client is None:
            import boto3  # noqa: PLC0415 — lazy import; boto3 is optional

            self._client = boto3.client("s3", region_name=self._region)
        return self._client

    def put_object(self, key: str, data: bytes) -> None:
        self._cli().put_object(Bucket=self._bucket, Key=key, Body=data)

    def get_object(self, key: str) -> bytes:
        """Return the object bytes for *key*.

        Raises ``KeyError(key)`` when the key does not exist — same contract as
        ``InMemoryBlobStore`` (dict KeyError) and ``_Oss2BucketMixin``
        (oss2.NoSuchKey → KeyError).  boto3 raises a ``ClientError`` whose
        ``response["Error"]["Code"]`` is ``"NoSuchKey"``; we normalise that here
        so all callers (BlobChannel.is_ready, etc.) can rely on KeyError.
        """
        try:
            return self._cli().get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except Exception as exc:  # noqa: BLE001 — duck-typed ClientError
            if _get_error_code(exc) == "NoSuchKey":
                raise KeyError(key) from exc
            raise

    def list_prefix(self, prefix: str) -> list[str]:
        """Return all keys under *prefix*, paginating list_objects_v2."""
        cli = self._cli()
        keys: list[str] = []
        paginator = cli.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def delete_object(self, key: str) -> None:
        self._cli().delete_object(Bucket=self._bucket, Key=key)

    def sign_url(self, key: str, expires: int = 3600, method: str = "GET") -> str:
        """Return a presigned URL for *key* (valid for *expires* seconds).

        Uses ``generate_presigned_url`` with the ``get_object`` operation.
        The *method* parameter is accepted for API parity with the Aliyun
        implementation but only ``"GET"`` maps to ``get_object`` presigning;
        other values are passed through for forward-compatibility.
        """
        operation = "get_object" if method.upper() == "GET" else "put_object"
        return str(
            self._cli().generate_presigned_url(
                operation,
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires,
            )
        )


class Ec2MetadataS3Client(S3Client):
    """S3 client for use inside an EC2 instance (probe-side).

    Identical to ``S3Client`` but relies on boto3's default credential chain,
    which automatically resolves the EC2 instance-profile IAM role via the
    instance metadata service (IMDSv2).  No static keys or custom provider
    needed — boto3 handles role discovery transparently.

    Unlike the Aliyun ``EcsRamRoleOssClient`` / ``_EcsMetadataCredentialsProvider``
    pair (which must hit the link-local metadata endpoint manually because
    alibabacloud_credentials is absent in the probe install), boto3's built-in
    credential chain already includes IMDSv2 instance-profile resolution, so no
    extra provider class is required here.

    Intended for the in-region EC2 probe only; the control plane uses plain
    ``S3Client`` with static / env credentials.
    """

    def __init__(self, bucket: str, region: str = "us-east-1") -> None:
        # client=None → lazy boto3 default chain (picks up instance profile)
        super().__init__(bucket, region, client=None)
