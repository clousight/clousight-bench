"""AWS campaign-probe lifecycle: EC2 carrier + S3 sync (probe-sink §7).

Mirrors ``aliyun._AliyunCampaignProbe`` / ``AliyunRuntimeProvider`` for AWS.
Constructor factories are injectable so tests run account-free (no real AWS
credentials required — pass a fake Ec2Sdk / S3Client).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clousight_bench.core.plugin import CampaignProbeHook

if TYPE_CHECKING:
    from clousight_bench.domains.agent_runtime.probe.oss_channel import OssChannel
    from clousight_bench.domains.agent_runtime.probe.oss_client import OssClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truthy(v: object) -> bool:
    """Interpret a target flag that may arrive as a bool or a YAML string."""
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def _published_code_spec() -> str:
    """The published-package ``code_spec`` pinned to the control plane's own version.

    Pinning avoids control-plane↔probe version skew: the probe installs the SAME
    version that is driving the campaign (protocol/token/S3-prefix contract).
    Falls back to the bare name if the version can't be read (e.g. odd install).
    """
    try:
        from importlib.metadata import version

        return f"clousight-bench[probe]=={version('clousight-bench')}"
    except Exception:  # noqa: BLE001 - metadata absent → bare name still installs
        return "clousight-bench[probe]"


# ---------------------------------------------------------------------------
# Campaign probe
# ---------------------------------------------------------------------------


class _AwsCampaignProbe(CampaignProbeHook):
    """Per-campaign probe lifecycle: EC2 carrier + S3 sync (probe-sink §7).

    Constructor factories are injectable so tests run account-free. The real
    path (``_default_carrier``) creates an :class:`Ec2ProbeCarrier` — a stock
    Amazon Linux 2023 EC2 instance whose cloud-init user-data ``pip install``s
    the public ``clousight-bench[probe]`` package (no container image).
    """

    def __init__(self, carrier_factory=None, oss_factory=None) -> None:
        self._carrier_factory = carrier_factory or self._default_carrier
        self._oss_factory = oss_factory or self._default_oss
        self._carrier: Any = None
        self._oss: OssClient | None = None
        self._channel: OssChannel | None = None  # OssChannel built during start_campaign_probe
        self._prefix = ""
        self._bucket = ""

    # ------------------------------------------------------------------
    # Default factories (real-cloud paths)
    # ------------------------------------------------------------------

    @staticmethod
    def _default_oss(target: dict) -> Any:  # noqa: ANN202
        from clousight_bench.domains.agent_runtime.probe.s3_client import S3Client

        bucket = str(target.get("oss_bucket") or "")
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
        _bucket = bucket or str(target.get("oss_bucket") or "")
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

    # ------------------------------------------------------------------
    # CampaignProbeHook interface
    # ------------------------------------------------------------------

    def start_campaign_probe(self, target: dict) -> dict[str, Any]:
        """Provision the probe.

        Returns ``{probe_control_prefix, probe_oss_prefix, probe_token,
        probe_in_vpc}`` for target stamping — no ``probe_url`` key (S3-mediated
        transport, no HTTP surface required).
        """
        from clousight_bench.domains.agent_runtime.probe.oss_channel import OssChannel

        run_id = str(target.get("run_id") or "")
        campaign_id = run_id or "adhoc"
        self._bucket = str(target.get("oss_bucket") or "")
        self._prefix = f"clousight-bench/telemetry/{campaign_id}/"
        self._oss = self._oss_factory(target)
        # Build the control channel — readiness is polled via S3, not HTTP.
        channel = OssChannel(self._oss, campaign_id)
        self._channel = channel
        # Clear any residue from a prior run on this (possibly reused) campaign
        # prefix — a stale `stop` sentinel would make the fresh probe exit at once.
        channel.reset()
        self._carrier = self._carrier_factory(target, self._prefix, campaign_id, self._bucket)
        # Inject the readiness check so provision() polls S3 (not IAM).
        self._carrier.ready_check = channel.is_ready
        self._carrier.provision()  # raises CarrierError on failure
        return {
            "probe_control_prefix": campaign_id,
            "probe_oss_prefix": self._prefix,
            "probe_token": getattr(self._carrier, "token", "") or "",
            "probe_in_vpc": True,
        }

    def sync_probe_artifacts(self, results_dir: Any) -> None:
        """Mirror the probe's S3 prefix into results_dir (channel ②)."""
        if self._oss is None:
            return
        from clousight_bench.domains.agent_runtime.probe.oss_sync import sync_prefix

        sync_prefix(self._oss, self._prefix, results_dir)

    def stop_campaign_probe(self) -> None:
        """Reap the probe. Idempotent + best-effort (called from a finally).

        Sends the S3 stop sentinel BEFORE tearing down the EC2 carrier so the
        in-region loop gets a chance to drain gracefully.
        """
        if self._channel is not None:
            try:
                self._channel.signal_stop()
            except Exception:  # noqa: BLE001
                pass
            self._channel = None
        if self._carrier is not None:
            try:
                self._carrier.teardown()
            except Exception:  # noqa: BLE001
                pass
            self._carrier = None
