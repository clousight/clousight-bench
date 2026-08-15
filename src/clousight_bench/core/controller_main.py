"""cb-controller — the on-ECS entrypoint for the ecs prod profile.

Runs on the ephemeral controller instance. Claims the campaign, drives the
serial orchestration loop (wrapping ``core.orchestrator.execute`` per task), and
lets the self-destruct watchdog reap the run (runtimes + NAT + self) on
completion/timeout/stop.

``build`` is a factory seam that wires everything from an env dict + an
``OssClient`` WITHOUT running, so it is unit-testable with no cloud. ``main`` is
the thin live entrypoint (Oss2Client via the instance metadata role).
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from clousight_bench.core.campaign_spec import DEFAULT_WATCHDOG_TIMEOUT_S
from clousight_bench.core.controller import CampaignController, RunTask, TaskOutcome
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.resource_ledger import LEDGER_FILE, ResourceLedger
from clousight_bench.core.schema import RunSpec
from clousight_bench.core.watchdog import SelfDestructWatchdog
from clousight_bench.domains.agent_runtime.controller_reaper import (
    RestrictedReaper,
    live_runtimes_from_ledger,
)
from clousight_bench.domains.agent_runtime.probe.campaign_channel import CampaignChannel
from clousight_bench.domains.agent_runtime.probe.oss_client import OssClient

_OK_STATUSES = ("completed", "unsupported")
# Terraform names for the run's NAT + its EIP (controller.tf / main.tf). The
# reaper deletes them by name so the controller can self-clean with no laptop.
_NAT_NAME = "clousight-bench-nat"
_NAT_EIP_NAME = "clousight-bench-nat-eip"
_ECS_METADATA_INSTANCE_ID_URL = "http://100.100.100.200/latest/meta-data/instance-id"


def build_run_task(platform: str, results_dir: str | Path, *, allow_live: bool = True) -> RunTask:
    """A RunTask that runs one task through the full lifecycle and returns its
    serialized result (JSON + optional parquet sidecar) as a TaskOutcome."""
    rdir = Path(results_dir)

    def run_task(task_id: str, spec: Any) -> TaskOutcome:
        run_spec = RunSpec(
            domain="agent-runtime",
            task_id=task_id,
            platform=platform,
            target=dict(spec.target),
            params=dict(spec.params),
        )
        record = execute(run_spec, results_dir=rdir, allow_live=allow_live)
        result_json = record.to_json().encode("utf-8")
        sidecar = (
            rdir
            / record.identity.domain
            / record.identity.adapter
            / record.run.run_id
            / "series.parquet"
        )
        parquet = sidecar.read_bytes() if sidecar.exists() else None
        ok = record.status in _OK_STATUSES
        error = None if ok else (record.status if not record.errors else str(record.errors[0]))
        return TaskOutcome(task_id=task_id, ok=ok, result_json=result_json, series_parquet=parquet, error=error)

    return run_task


def _ledger_bytes_reader(results_dir: str | Path) -> Callable[[], bytes]:
    path = Path(results_dir) / LEDGER_FILE
    return lambda: path.read_bytes() if path.exists() else b""


def _ecs_metadata_instance_id(timeout: float = 5.0) -> str:  # pragma: no cover - live metadata
    """This controller's own instance id, read from the ECS metadata service."""
    import requests

    return requests.get(_ECS_METADATA_INSTANCE_ID_URL, timeout=timeout).text.strip()


def _live_delete_runtime(region: str) -> Callable[[str], None]:  # pragma: no cover - live SDK
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


def _live_delete_nat(region: str, nat_name: str, eip_name: str) -> Callable[[], None]:  # pragma: no cover - live SDK
    """Tear the run's NAT down in the order Aliyun requires: unassociate the EIP
    FIRST (a still-bound EIP makes DeleteNatGateway fail even with force=true),
    then force-delete the NAT (drops its SNAT/DNAT entries), then release the EIP.

    Each step is best-effort + waits briefly for the async unassociate/delete to
    settle so the next step isn't blocked by the previous still being in-flight.
    The local `csbench teardown` (terraform destroy) is the backstop for anything
    left."""

    def _del() -> None:
        from alibabacloud_tea_openapi import models as open_api_models
        from alibabacloud_credentials.client import Client as CredClient
        from alibabacloud_vpc20160428 import models as vm
        from alibabacloud_vpc20160428.client import Client

        cfg = open_api_models.Config(credential=CredClient())
        cfg.endpoint = f"vpc.{region}.aliyuncs.com"
        c = Client(cfg)
        nat_ids = [
            n.nat_gateway_id
            for n in (
                c.describe_nat_gateways(vm.DescribeNatGatewaysRequest(region_id=region, name=nat_name)).body.nat_gateways.nat_gateway
                or []
            )
        ]
        eips = list(
            c.describe_eip_addresses(vm.DescribeEipAddressesRequest(region_id=region, eip_name=eip_name)).body.eip_addresses.eip_address
            or []
        )
        # 1) unassociate each bound EIP from its NAT (force), then let it settle.
        unbound = False
        for eip in eips:
            if eip.status in ("InUse", "Associating"):
                with contextlib.suppress(Exception):
                    c.unassociate_eip_address(
                        vm.UnassociateEipAddressRequest(
                            allocation_id=eip.allocation_id,
                            instance_id=eip.instance_id,
                            instance_type=eip.instance_type or "Nat",
                            force=True,
                        )
                    )
                    unbound = True
        if unbound:
            time.sleep(8)
        # 2) force-delete the NAT (removes SNAT/DNAT), then let it settle.
        deleted = False
        for nid in nat_ids:
            with contextlib.suppress(Exception):
                c.delete_nat_gateway(vm.DeleteNatGatewayRequest(nat_gateway_id=nid, force=True))
                deleted = True
        if deleted:
            time.sleep(8)
        # 3) release the (now-free) EIP.
        for eip in eips:
            with contextlib.suppress(Exception):
                c.release_eip_address(vm.ReleaseEipAddressRequest(allocation_id=eip.allocation_id))

    return _del


def _live_delete_self(region: str) -> Callable[[str], None]:  # pragma: no cover - live SDK
    """Delete the controller's own ECS instance (called LAST by the reaper)."""

    def _del(instance_id: str) -> None:
        from alibabacloud_ecs20140526 import models as em
        from alibabacloud_ecs20140526.client import Client
        from alibabacloud_credentials.client import Client as CredClient
        from alibabacloud_tea_openapi import models as open_api_models

        cfg = open_api_models.Config(credential=CredClient())
        cfg.endpoint = f"ecs.{region}.aliyuncs.com"
        Client(cfg).delete_instance(em.DeleteInstanceRequest(instance_id=instance_id, force=True))

    return _del


def build_reaper(
    env: dict[str, str],
    *,
    results_dir: str | Path,
    instance_id: str | None = None,
    live_runtimes: Callable[[], list[str]] | None = None,
    delete_runtime: Callable[[str], None] | None = None,
    delete_nat: Callable[[], None] | None = None,
    delete_self: Callable[[str], None] | None = None,
) -> RestrictedReaper:
    """Compose the self-destruct reaper: runtimes (from the ledger) → NAT → self.

    Every collaborator has a live default (instance-role SDK) and a test seam.
    Delegating the delete ORDER + best-effort semantics to :class:`RestrictedReaper`.
    """
    region = env.get("CB_REGION", "cn-hangzhou")
    ledger_dir = Path(results_dir)
    lr = live_runtimes or (lambda: live_runtimes_from_ledger(ResourceLedger(ledger_dir)))
    iid = instance_id if instance_id is not None else _ecs_metadata_instance_id()
    return RestrictedReaper(
        live_runtimes=lr,
        delete_runtime=delete_runtime or _live_delete_runtime(region),
        delete_nat=delete_nat or _live_delete_nat(region, _NAT_NAME, _NAT_EIP_NAME),
        delete_self=delete_self or _live_delete_self(region),
        self_instance_id=iid,
    )


def build(
    env: dict[str, str],
    oss: OssClient,
    *,
    run_task: RunTask | None = None,
    reaper: Any | None = None,
    now: Callable[[], float] = time.time,
) -> tuple[CampaignController, SelfDestructWatchdog]:
    """Wire the controller + watchdog from env + OSS. No side effects."""
    campaign_id = env["CB_CAMPAIGN_ID"]
    results_dir = env.get("CB_RESULTS_DIR", "/var/lib/cb/results")
    platform = env.get("CB_PLATFORM", "aliyun-agentrun")
    channel = CampaignChannel(oss, campaign_id, now=now)

    rt = run_task or build_run_task(platform, results_dir)
    controller = CampaignController(
        channel, rt, now=now, ledger_bytes=_ledger_bytes_reader(results_dir)
    )

    spec = channel.read_launch()
    timeout_s = spec.watchdog_timeout_s if spec else DEFAULT_WATCHDOG_TIMEOUT_S
    reap = reaper.reap if reaper is not None else (lambda: None)
    watchdog = SelfDestructWatchdog(channel, reap=reap, timeout_s=timeout_s, now=now)
    return controller, watchdog


def main() -> int:  # pragma: no cover - live entrypoint, exercised by the smoke runbook
    from clousight_bench.domains.agent_runtime.probe.oss_client import EcsRamRoleOssClient

    import traceback

    env = dict(os.environ)
    # In-region controller reads/writes OSS over the VPC-internal endpoint. Creds
    # come from THIS instance's RAM role read straight from the ECS metadata
    # service (requests-only, 5s timeout) — NOT the alibabacloud_credentials
    # default chain (Oss2Client), whose ECS-metadata provider blocks silently with
    # no timeout when it can't resolve, hanging the controller before its first
    # OSS write (observed live 2026-08-15: zero boot markers, no traceback).
    oss = EcsRamRoleOssClient(env["CB_OSS_BUCKET"], env.get("CB_REGION", "cn-hangzhou"))
    channel = CampaignChannel(oss, env["CB_CAMPAIGN_ID"])
    # Boot markers to OSS: the controller's stdout/stderr don't reach the serial
    # console, so log progress here — a `csbench logs` shows exactly how far it got.
    try:
        channel.append_log("controller boot: python+import+oss OK")
        if not channel.claim():
            channel.append_log("campaign already claimed — exiting")
            return 0  # another controller already owns this campaign
        channel.append_log("claimed campaign; building controller+watchdog")
        # Self-destruct reaper: on watchdog-terminal it deletes residual runtimes
        # → NAT → this instance (last), so a finished/timed-out run leaves nothing
        # even if the laptop is off. Best-effort to build — if the metadata/SDK
        # wiring fails we log it and fall back to a noop reap rather than refuse to
        # run the campaign (the local `csbench teardown` is the backstop).
        reaper: Any | None = None
        try:
            results_dir = env.get("CB_RESULTS_DIR", "/var/lib/cb/results")
            reaper = build_reaper(env, results_dir=results_dir)
            channel.append_log(f"reaper armed (self={reaper._self_instance_id})")
        except Exception as exc:  # noqa: BLE001 - never let reaper-build block the run
            channel.append_log(f"reaper build failed ({exc!r}); teardown falls to local backstop")
        controller, watchdog = build(env, oss, reaper=reaper)
        channel.append_log("built; starting orchestration loop + watchdog")
        start = time.time()
        threading.Thread(target=controller.run, daemon=True).start()
        reason = watchdog.run_until_terminal(start)
        channel.append_log(f"watchdog terminal: {reason}")
        return 0
    except Exception:
        with contextlib.suppress(Exception):
            channel.append_log("CONTROLLER FATAL:\n" + traceback.format_exc()[-3000:])
        raise


if __name__ == "__main__":  # pragma: no cover - `python -m clousight_bench.core.controller_main`
    # cloud-init boots the controller via `python3.11 -m ...`; without this guard
    # that only imports the module and exits WITHOUT calling main() (observed live
    # 2026-08-15: controller wrote zero OSS, no stdout — main() never ran).
    raise SystemExit(main())
