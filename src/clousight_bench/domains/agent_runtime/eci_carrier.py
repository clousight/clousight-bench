from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class EciSdk(Protocol):
    """The three ECI operations the carrier needs. Plan 5 supplies a real impl
    over alibabacloud_eci20180808; tests inject FakeEciSdk. Account-free seam."""

    def create_container_group(self, req: dict[str, Any]) -> str: ...  # -> instance_id
    def describe_container_group(self, instance_id: str) -> dict[str, Any]: ...  # {"status","public_ip"}
    def delete_container_group(self, instance_id: str) -> None: ...


@dataclass
class EciCarrierConfig:
    """Everything the create request needs. Real values pinned in Plan 5."""

    image: str = "registry.cn-hangzhou.aliyuncs.com/library/python:3.12"
    # OSS control-plane fields (required for private probe)
    bucket: str = ""  # OSS bucket name for CB_PROBE_BUCKET
    campaign_id: str = ""  # per-campaign id; becomes CB_PROBE_CONTROL_PREFIX
    # Kept for caller compatibility (accepted but unused — a later task removes them)
    oss_code_uri: str = ""  # oss://bucket/campaign-<id>/cb-probe.zip
    code_sha256: str = ""  # expected sha256 of cb-probe.zip (fail-closed)
    region: str = "cn-hangzhou"
    vswitch_id: str = ""
    security_group_id: str = ""
    ram_role: str = ""  # instance RAM role granting oss:PutObject / oss:GetObject
    cpu: float = 2.0
    memory: float = 4.0
    port: int = 9000
    ready_timeout_s: float = 180.0
    poll_interval_s: float = 3.0
    run_id: str | None = None


def _split_oss_uri(uri: str) -> tuple[str, str]:
    """``oss://bucket/path/to/obj`` -> ``("bucket", "path/to/obj")``; "" -> ("","")."""
    if not uri:
        return "", ""
    ref = uri.removeprefix("oss://")
    bucket, _, obj = ref.partition("/")
    return bucket, obj


class CarrierError(RuntimeError):
    """Provision failed (spec §9: no silent fallback)."""


@dataclass
class EciProbeCarrier:
    sdk: EciSdk
    config: EciCarrierConfig
    # ready_check() -> True once the OSS heartbeat key exists (ECI loop is running).
    # Defaults to None; provision() builds the default from config if not supplied.
    ready_check: Callable[[], bool] | None = None
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.monotonic
    instance_id: str | None = field(default=None, init=False)
    control_prefix: str | None = field(default=None, init=False)  # OSS campaign_id / control prefix
    token: str = field(default="", init=False)  # bearer token injected into the ECI env

    def provision(self) -> str:
        """Create the ECI, wait until Running AND the OSS readiness heartbeat fires.

        Returns the control prefix (campaign_id) that both the carrier and the
        ECI loop use to scope their OSS channel.  Raises CarrierError on timeout
        (never silently falls back — on timeout, tears down + raises).
        """
        import secrets

        # Per-probe token passed to the ECI via CB_PROBE_TOKEN.
        self.token = secrets.token_urlsafe(32)
        req = self._build_create_request()
        self.instance_id = self.sdk.create_container_group(req)
        deadline = self.now() + self.config.ready_timeout_s
        ready = self.ready_check if self.ready_check is not None else self._default_ready_check()
        while self.now() < deadline:
            desc = self.sdk.describe_container_group(self.instance_id)
            status = str(desc.get("status") or "")
            if status == "Running" and ready():
                self.control_prefix = self.config.campaign_id
                return self.config.campaign_id
            self.sleep(self.config.poll_interval_s)
        self.teardown()  # don't leak a half-booted instance
        raise CarrierError(f"ECI probe {self.instance_id} not ready within {self.config.ready_timeout_s}s")

    def teardown(self) -> None:
        """Reap the ECI. Idempotent + best-effort (called from a finally)."""
        iid, self.instance_id = self.instance_id, None
        self.control_prefix = None
        if iid is None:
            return
        try:
            self.sdk.delete_container_group(iid)
        except Exception:  # noqa: BLE001 — teardown must never raise out of finally
            pass

    def _build_create_request(self) -> dict[str, Any]:
        """Build a private ECI create request.

        The container is fully private: no EIP is requested, only
        v_switch_id + security_group_id. The ACR image's ENTRYPOINT already
        runs ``python -m clousight_bench.domains.agent_runtime.probe.agent_loop``
        so no ``command`` override is needed. Env vars are wired to the exact
        names that agent_loop.main() reads.
        """
        c = self.config
        return {
            "region_id": c.region,
            "container_group_name": f"cb-probe-{(c.run_id or 'adhoc')[-8:]}",
            "cpu": c.cpu,
            "memory": c.memory,
            "v_switch_id": c.vswitch_id,
            "security_group_id": c.security_group_id,
            "ram_role_name": c.ram_role,
            "restart_policy": "Never",
            "tags": [
                {"key": "clousight-bench:managed", "value": "true"},
                {"key": "clousight-bench:run-id", "value": c.run_id or ""},
            ],
            "container": [
                {
                    "name": "cb-probe",
                    "image": c.image,
                    # No "command" key: the ACR image ENTRYPOINT runs agent_loop.
                    "environment_var": [
                        {"key": "CB_PROBE_BUCKET", "value": c.bucket},
                        {"key": "CB_PROBE_REGION", "value": c.region},
                        {"key": "CB_PROBE_CONTROL_PREFIX", "value": c.campaign_id},
                        {"key": "CB_PROBE_TOKEN", "value": self.token},
                    ],
                }
            ],
        }

    def _default_ready_check(self) -> Callable[[], bool]:
        """Build the default OSS-heartbeat readiness check from config.

        Constructs an OssChannel pointed at config.bucket / config.region /
        config.campaign_id and returns channel.is_ready as the callable.
        Lazy import so production code that skips OSS doesn't need oss2 at
        import time.
        """
        from clousight_bench.domains.agent_runtime.probe.oss_channel import OssChannel
        from clousight_bench.domains.agent_runtime.probe.oss_client import EcsRamRoleOssClient

        oss = EcsRamRoleOssClient(bucket=self.config.bucket, region=self.config.region)
        channel = OssChannel(oss, campaign_id=self.config.campaign_id)
        return channel.is_ready


class Eci20180808Sdk:
    """Real EciSdk over alibabacloud_eci20180808 (live path; Phase B).

    Maps the carrier's plain-dict create request onto ECI request models and
    normalizes describe to the {"status","public_ip"} contract the carrier
    expects. The SDK client is injectable so this mapping is unit-tested with a
    fake client (no network); production builds a real Client from the default
    credential chain (env / RAM-user AK / instance role).

    Lazy-import strategy: both the ECI Client and the models module are imported
    on first use inside methods.  In the test path the injected fake client means
    _cli() never touches alibabacloud_eci20180808, and the model objects are built
    via _make (types.SimpleNamespace) so the package need not be installed.
    In the live path _make delegates to real SDK model constructors."""

    def __init__(self, region: str = "cn-hangzhou", *, client: Any | None = None) -> None:
        self._region = region
        self._client = client

    def _cli(self) -> Any:
        if self._client is None:
            from alibabacloud_credentials.client import Client as CredClient
            from alibabacloud_eci20180808.client import Client as EciClient
            from alibabacloud_tea_openapi import models as open_api_models

            cfg = open_api_models.Config(credential=CredClient())
            cfg.endpoint = f"eci.{self._region}.aliyuncs.com"
            self._client = EciClient(cfg)
        return self._client

    @staticmethod
    def _make(model_cls: Any, **kwargs: Any) -> Any:
        """Construct a model object.

        When the SDK package is present (Phase B live path), model_cls is a real
        SDK model class; constructor errors propagate immediately so callers
        discover bad field names / types at call time rather than sending a
        malformed request to the live API.

        When the SDK package is absent (test path, fake client injected),
        _models() returns a _NoneProxy whose attribute access yields None, so
        model_cls is None here; we return a types.SimpleNamespace stand-in that
        satisfies the duck-typed protocol of the fake client."""
        import types as _types

        if model_cls is None:
            return _types.SimpleNamespace(**kwargs)
        return model_cls(**kwargs)

    def _models(self) -> Any:
        """Return the alibabacloud_eci20180808.models module, or a SimpleNamespace
        proxy whose attributes are None (test path where the package is absent
        and a fake client has been injected)."""
        try:
            from alibabacloud_eci20180808 import models as m

            return m
        except ImportError:
            # Package not installed — only valid when a fake client is injected.
            # Return a proxy that yields None for any attribute (model class name).
            class _NoneProxy:
                def __getattr__(self, _: str) -> None:
                    return None

            return _NoneProxy()

    def create_container_group(self, req: dict[str, Any]) -> str:
        m = self._models()

        containers = []
        for c in req.get("container", []):
            ports = [
                self._make(
                    m.CreateContainerGroupRequestContainerPort,
                    port=p["port"],
                    protocol=p.get("protocol", "TCP"),
                )
                for p in c.get("port", [])
            ]
            envs = [
                self._make(
                    m.CreateContainerGroupRequestContainerEnvironmentVar, key=e["key"], value=e["value"]
                )
                for e in c.get("environment_var", [])
            ]
            containers.append(
                self._make(
                    m.CreateContainerGroupRequestContainer,
                    name=c["name"],
                    image=c["image"],
                    port=ports,
                    command=list(c.get("command", [])),
                    environment_var=envs,
                )
            )
        tags = [
            self._make(m.CreateContainerGroupRequestTag, key=t["key"], value=t["value"])
            for t in req.get("tags", [])
        ]
        request = self._make(
            m.CreateContainerGroupRequest,
            region_id=req["region_id"],
            container_group_name=req["container_group_name"],
            cpu=float(req["cpu"]),
            memory=float(req["memory"]),
            v_switch_id=req.get("v_switch_id") or None,
            security_group_id=req.get("security_group_id") or None,
            ram_role_name=req.get("ram_role_name") or None,
            restart_policy=req.get("restart_policy", "Never"),
            container=containers,
            tag=tags,
        )
        resp = self._cli().create_container_group(request)
        return str(resp.body.container_group_id)

    def describe_container_group(self, instance_id: str) -> dict[str, Any]:
        import json as _json

        m = self._models()

        request = self._make(
            m.DescribeContainerGroupsRequest,
            region_id=self._region,
            container_group_ids=_json.dumps([instance_id]),
        )
        resp = self._cli().describe_container_groups(request)
        groups = getattr(resp.body, "container_groups", None) or []
        if not groups:
            return {"status": "", "public_ip": ""}
        g = groups[0]
        return {
            "status": str(getattr(g, "status", "") or ""),
            "public_ip": str(getattr(g, "internet_ip", "") or ""),
        }

    def delete_container_group(self, instance_id: str) -> None:
        m = self._models()

        request = self._make(
            m.DeleteContainerGroupRequest, region_id=self._region, container_group_id=instance_id
        )
        self._cli().delete_container_group(request)
