"""Aliyun runtime-provider plugin + per-campaign ECS probe hook."""

from __future__ import annotations

from collections.abc import Callable

from clousight_bench.core.plugin import ControllerReaperSpec, ControllerTfSpec
from clousight_bench.domains.agent_runtime.aliyun._shared import (
    Any,
    CampaignProbeOrchestrator,
    Ecs20140526Sdk,
    EcsCarrierConfig,
    EcsProbeCarrier,
    RuntimeProviderPlugin,
    _published_code_spec,
    _truthy,
)
from clousight_bench.domains.agent_runtime.aliyun.transport import AliyunAgentRunTransport

# The infra/terraform/aliyun-iam prod-controller profile's terraform surface
# (`csbench submit` / `teardown`). Only the prod-profile resources — never a
# bare apply/destroy (which would in-place touch the mock FC + everything else
# in the module).
_CONTROLLER_TF_TARGETS = (
    "alicloud_instance.controller",
    "alicloud_ram_role.controller",
    "alicloud_ram_policy.controller",
    "alicloud_ram_role_policy_attachment.controller",
    "alicloud_nat_gateway.bench",
    "alicloud_eip_address.nat",
    "alicloud_eip_association.nat",
    "alicloud_snat_entry.bench",
)

# Plan yaml `driver:` keys → the controller_* terraform vars they set. Only the
# keys present in the plan are forwarded; absent keys keep the tf defaults.
_DRIVER_TF_VARS = {
    "install_docker": "controller_install_docker",
    "system_disk_size": "controller_system_disk_size",
    "docker_registry_mirror": "controller_docker_registry_mirror",
    "hf_endpoint": "controller_hf_endpoint",
    "instance_type": "controller_instance_type",
}


class _AliyunCampaignProbe(CampaignProbeOrchestrator):
    """Per-campaign probe lifecycle: ECS carrier + OSS sync (probe-sink §7).

    The real path (``_default_carrier``) creates an :class:`EcsProbeCarrier` — a
    stock-OS ECS instance whose cloud-init user-data ``pip install``s the public
    ``clousight-bench[probe]`` package (no container image, see docs/probe-carrier.md).
    The start/sync/stop lifecycle is inherited from ``CampaignProbeOrchestrator``.
    """

    @staticmethod
    def _default_carrier(target: dict, prefix: str, campaign_id: str = "", bucket: str = ""):  # noqa: ANN202
        run_id = str(target.get("run_id") or "")
        _bucket = bucket or str(target.get("blob_bucket") or "")
        region = str(target.get("region") or "cn-hangzhou")
        cid = campaign_id or run_id or "adhoc"
        code_spec, extra_deps = _AliyunCampaignProbe._resolve_code_spec(target, _bucket, region, cid)
        cfg = EcsCarrierConfig(
            bucket=_bucket,
            campaign_id=cid,
            region=region,
            vswitch_id=str(target.get("eci_vswitch_id") or ""),
            security_group_id=str(target.get("eci_security_group_id") or ""),
            ram_role=str(target.get("eci_probe_role") or ""),
            image_id=str(target.get("ecs_image_id") or ""),  # stock Aliyun OS image
            instance_type=str(target.get("ecs_instance_type") or "ecs.e-c1m2.large"),
            code_spec=code_spec,
            extra_deps=extra_deps,
            run_id=run_id or None,
        )
        return EcsProbeCarrier(sdk=Ecs20140526Sdk(region=region), config=cfg)

    @staticmethod
    def _resolve_code_spec(target: dict, bucket: str, region: str, campaign_id: str) -> tuple[str, list[str]]:
        """Resolve the carrier's ``(code_spec, extra_deps)``.

        Default: install the published ``clousight-bench[probe]`` (or an explicit
        ``probe_code_spec``) from the mirror — no extra_deps needed.

        Dev-wheel fallback (``probe_dev_wheel`` truthy): build a wheel of the
        current source, upload it to OSS, and use a presigned internal URL as the
        code_spec. A wheel URL can't carry the ``[probe]`` extra, so the extra's
        deps are returned separately for the cloud-init to install from the mirror.
        """
        if _truthy(target.get("probe_dev_wheel")):
            from clousight_bench.domains.agent_runtime.dev_wheel import probe_extra_deps, upload_dev_wheel
            from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client

            upload = Oss2Client(bucket=bucket, region=region)  # public endpoint → PUT
            signer = Oss2Client(bucket=bucket, region=region, internal=True)  # internal host → URL
            return upload_dev_wheel(upload, signer, campaign_id), probe_extra_deps()
        return str(target.get("probe_code_spec") or _published_code_spec()), []

    @staticmethod
    def _default_store(target: dict):  # noqa: ANN202
        from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client

        bucket = str(target.get("blob_bucket") or "")
        region = str(target.get("region") or "cn-hangzhou")
        return Oss2Client(bucket=bucket, region=region)


class AliyunRuntimeProvider(RuntimeProviderPlugin):
    """Registered for provider ``aliyun`` via the runtime_providers entry point."""

    provider = "aliyun"

    def build_transport(self, adapter: Any) -> AliyunAgentRunTransport:
        return AliyunAgentRunTransport(adapter)

    def controller_tf_spec(self) -> ControllerTfSpec:
        """The aliyun-iam prod-controller module's terraform surface."""
        return ControllerTfSpec(tf_targets=_CONTROLLER_TF_TARGETS, driver_tf_vars=_DRIVER_TF_VARS)

    def controller_reaper_spec(
        self, region: str, log: Callable[[str], None]
    ) -> ControllerReaperSpec:
        """SDK-backed delete callables for the Aliyun prod-controller reaper:
        AgentRuntime → NAT/EIP (Aliyun teardown order) → this ECS instance."""
        from clousight_bench.domains.agent_runtime.aliyun.controller_reaper_live import (
            NAT_EIP_NAME,
            NAT_NAME,
            ecs_metadata_instance_id,
            live_delete_nat,
            live_delete_runtime,
            live_delete_self,
        )

        return ControllerReaperSpec(
            delete_runtime=live_delete_runtime(region),
            delete_nat=live_delete_nat(region, NAT_NAME, NAT_EIP_NAME, log),
            delete_self=live_delete_self(region),
            self_instance_id=ecs_metadata_instance_id,
        )

    def campaign_probe_hook(
        self,
        carrier_factory=None,
        store_factory=None,
    ) -> _AliyunCampaignProbe:
        """Return an injectable ``_AliyunCampaignProbe``.

        ``carrier_factory`` / ``store_factory`` are forwarded to the probe so
        tests can inject fakes without touching the real ECI/OSS SDKs.
        Called by ``core.plugin.campaign_probe_hook`` with no args (real mode);
        tests call it directly with injected fakes.
        """
        return _AliyunCampaignProbe(
            carrier_factory=carrier_factory,
            store_factory=store_factory,
        )
