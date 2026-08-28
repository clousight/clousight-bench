"""Aliyun SDK-backed delete callables for the prod-controller reaper.

The vendor half of the ``ControllerReaperSpec`` seam: the neutral
``RestrictedReaper`` (in ``domains/agent_runtime/controller_reaper.py``) owns the
delete ORDER + best-effort semantics; these functions own the Aliyun SDK calls,
resource names, endpoints and VPC-teardown ordering. They used to live in
``core/controller_main.py``; moving them here keeps ``core`` free of any
``alibabacloud`` import (module-level OR lazy). See
``AliyunRuntimeProvider.controller_reaper_spec``.

The SDK is imported lazily inside each closure, so this module imports (and the
provider registers) without the SDK installed — every body is ``# pragma: no
cover`` since it only runs against a live account on the controller instance.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable

# Terraform names for the run's NAT + its EIP (controller.tf / main.tf). The
# reaper deletes them by name so the controller can self-clean with no laptop.
NAT_NAME = "clousight-bench-nat"
NAT_EIP_NAME = "clousight-bench-nat-eip"
_ECS_METADATA_INSTANCE_ID_URL = "http://100.100.100.200/latest/meta-data/instance-id"


def ecs_metadata_instance_id(timeout: float = 5.0) -> str:  # pragma: no cover - live metadata
    """This controller's own instance id, read from the ECS metadata service."""
    import requests

    return requests.get(_ECS_METADATA_INSTANCE_ID_URL, timeout=timeout).text.strip()


def live_delete_runtime(region: str) -> Callable[[str], None]:  # pragma: no cover - live SDK
    """Delete one AgentRuntime (endpoint first, best-effort) via the instance role."""

    def _del(runtime_id: str) -> None:
        from alibabacloud_agentrun20250910 import models as m
        from alibabacloud_agentrun20250910.client import Client
        from alibabacloud_credentials.client import Client as CredClient
        from alibabacloud_tea_openapi import models as open_api_models

        cfg = open_api_models.Config(credential=CredClient())
        cfg.region_id = region  # SDK raises "RegionId is empty" without this
        client = Client(cfg)
        with contextlib.suppress(Exception):
            client.delete_agent_runtime_endpoint(runtime_id, "Default", m.DeleteAgentRuntimeEndpointRequest())
        client.delete_agent_runtime(runtime_id)  # takes the id string, not a Request

    return _del


def live_delete_nat(  # pragma: no cover - live SDK
    region: str, nat_name: str, eip_name: str, log: Callable[[str], None] = lambda _m: None
) -> Callable[[], None]:
    """Tear the run's NAT down in the order Aliyun requires: unassociate the EIP
    FIRST (a still-bound EIP makes DeleteNatGateway fail even with force=true),
    then force-delete the NAT (drops its SNAT/DNAT entries), then release the EIP.

    Each step is best-effort — its error is logged (``log``) BEFORE the reaper
    moves on to delete_self (so it reaches OSS before this box dies) — with a
    brief settle between the async unassociate/delete. The local `csbench
    teardown` (terraform destroy) is the backstop for anything left."""

    def _del() -> None:
        from alibabacloud_credentials.client import Client as CredClient
        from alibabacloud_tea_openapi import models as open_api_models
        from alibabacloud_vpc20160428 import models as vm
        from alibabacloud_vpc20160428.client import Client

        cfg = open_api_models.Config(credential=CredClient())
        cfg.endpoint = f"vpc.{region}.aliyuncs.com"
        c = Client(cfg)
        log("delete_nat: start")  # first line before ANY SDK call, so a failing describe still surfaces
        try:
            nat_ids = [
                n.nat_gateway_id
                for n in (
                    c.describe_nat_gateways(
                        vm.DescribeNatGatewaysRequest(region_id=region, name=nat_name)
                    ).body.nat_gateways.nat_gateway
                    or []
                )
            ]
            eips = list(
                c.describe_eip_addresses(
                    vm.DescribeEipAddressesRequest(region_id=region, eip_name=eip_name)
                ).body.eip_addresses.eip_address
                or []
            )
        except Exception as exc:  # noqa: BLE001 - describe itself can be denied
            log(f"delete_nat describe FAILED: {exc}")
            return
        log(f"delete_nat: nats={nat_ids} eips={[(e.allocation_id, e.status) for e in eips]}")
        # 1) unassociate each bound EIP from its NAT (force), then let it settle.
        unbound = False
        for eip in eips:
            if eip.status in ("InUse", "Associating"):
                try:
                    c.unassociate_eip_address(
                        vm.UnassociateEipAddressRequest(
                            allocation_id=eip.allocation_id,
                            instance_id=eip.instance_id,
                            instance_type=eip.instance_type or "Nat",
                            force=True,
                        )
                    )
                    unbound = True
                except Exception as exc:  # noqa: BLE001 - log then continue best-effort
                    log(f"delete_nat unassociate {eip.allocation_id} FAILED: {exc}")
        if unbound:
            time.sleep(8)
        # 2) force-delete the NAT (removes SNAT/DNAT), then let it settle.
        deleted = False
        for nid in nat_ids:
            try:
                c.delete_nat_gateway(vm.DeleteNatGatewayRequest(nat_gateway_id=nid, force=True))
                deleted = True
            except Exception as exc:  # noqa: BLE001
                log(f"delete_nat delete {nid} FAILED: {exc}")
        if deleted:
            time.sleep(8)
        # 3) release the (now-free) EIP.
        for eip in eips:
            try:
                c.release_eip_address(vm.ReleaseEipAddressRequest(allocation_id=eip.allocation_id))
            except Exception as exc:  # noqa: BLE001
                log(f"delete_nat release {eip.allocation_id} FAILED: {exc}")

    return _del


def live_delete_self(region: str) -> Callable[[str], None]:  # pragma: no cover - live SDK
    """Delete the controller's own ECS instance (called LAST by the reaper)."""

    def _del(instance_id: str) -> None:
        from alibabacloud_credentials.client import Client as CredClient
        from alibabacloud_ecs20140526 import models as em
        from alibabacloud_ecs20140526.client import Client
        from alibabacloud_tea_openapi import models as open_api_models

        cfg = open_api_models.Config(credential=CredClient())
        cfg.endpoint = f"ecs.{region}.aliyuncs.com"
        Client(cfg).delete_instance(em.DeleteInstanceRequest(instance_id=instance_id, force=True))

    return _del
