"""AWS campaign-probe lifecycle: EC2 carrier + S3 sync (probe-sink §7).

Mirrors ``aliyun._AliyunCampaignProbe`` / ``AliyunRuntimeProvider`` for AWS.
Constructor factories are injectable so tests run account-free (no real AWS
credentials required — pass a fake Ec2Sdk / S3Client). The start/sync/stop
lifecycle lives in ``CampaignProbeOrchestrator``; this module supplies only the
AWS-specific blob client, carrier and dev-wheel code-spec resolution.
"""

from __future__ import annotations

from typing import Any

from clousight_bench.domains.agent_runtime.campaign_probe_base import (
    CampaignProbeOrchestrator,
    _published_code_spec,
    _truthy,
)


class _AwsCampaignProbe(CampaignProbeOrchestrator):
    """Per-campaign probe lifecycle: EC2 carrier + S3 sync (probe-sink §7).

    The real path (``_default_carrier``) creates an :class:`Ec2ProbeCarrier` — a
    stock Amazon Linux 2023 EC2 instance whose cloud-init user-data ``pip
    install``s the public ``clousight-bench[probe]`` package (no container image).
    """

    @staticmethod
    def _default_store(target: dict) -> Any:  # noqa: ANN202
        from clousight_bench.domains.agent_runtime.probe.s3_client import S3Client

        bucket = str(target.get("blob_bucket") or "")
        region = str(target.get("region") or "us-east-1")
        return S3Client(bucket=bucket, region=region)

    @staticmethod
    def _resolve_code_spec(target: dict, bucket: str, region: str, campaign_id: str) -> tuple[str, list[str]]:
        """Resolve the carrier's ``(code_spec, extra_deps)``.

        Default: install the published ``clousight-bench[probe]`` (or an explicit
        ``probe_code_spec``) from PyPI — no extra_deps needed.

        Dev-wheel fallback (``probe_dev_wheel`` truthy): build a wheel of the
        current source, upload it to S3, and use a presigned URL as the
        code_spec.  A wheel URL can't carry the ``[probe]`` extra, so the extra's
        deps are returned separately for the cloud-init to install from PyPI.

        S3 presigning is regional; a single ``S3Client`` handles both upload
        and presign (no internal/public endpoint split needed unlike Aliyun OSS).
        """
        if _truthy(target.get("probe_dev_wheel")):
            from clousight_bench.domains.agent_runtime.dev_wheel import probe_extra_deps, upload_dev_wheel
            from clousight_bench.domains.agent_runtime.probe.s3_client import S3Client

            # One S3Client suffices for both PUT and presign (no VPC-internal
            # endpoint distinction for S3 — the presigned URL uses the regional
            # endpoint and is reachable from within the VPC via the VPC endpoint
            # or NAT gateway).
            client = S3Client(bucket=bucket, region=region)
            return upload_dev_wheel(client, client, campaign_id), probe_extra_deps()
        return str(target.get("probe_code_spec") or _published_code_spec()), []

    @staticmethod
    def _default_carrier(target: dict, prefix: str, campaign_id: str = "", bucket: str = "") -> Any:  # noqa: ANN202
        from clousight_bench.domains.agent_runtime.aws.carrier import (
            Boto3Ec2Sdk,
            Ec2CarrierConfig,
            Ec2ProbeCarrier,
        )

        run_id = str(target.get("run_id") or "")
        _bucket = bucket or str(target.get("blob_bucket") or "")
        region = str(target.get("region") or "us-east-1")
        cid = campaign_id or run_id or "adhoc"
        code_spec, extra_deps = _AwsCampaignProbe._resolve_code_spec(target, _bucket, region, cid)
        cfg = Ec2CarrierConfig(
            bucket=_bucket,
            campaign_id=cid,
            region=region,
            subnet_id=str(target.get("probe_subnet_id") or ""),
            security_group_id=str(target.get("probe_security_group_id") or ""),
            iam_instance_profile=str(target.get("probe_instance_profile") or ""),
            image_id=str(target.get("ec2_image_id") or ""),  # stock Amazon Linux 2023 AMI
            instance_type=str(target.get("ec2_instance_type") or "t3.small"),
            code_spec=code_spec,
            extra_deps=extra_deps,
            run_id=run_id or None,
        )
        return Ec2ProbeCarrier(sdk=Boto3Ec2Sdk(region=region), config=cfg)
