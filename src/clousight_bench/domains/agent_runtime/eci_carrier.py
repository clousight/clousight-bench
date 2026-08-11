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
    oss_code_uri: str = ""  # oss://bucket/campaign-<id>/cb-probe.zip
    code_sha256: str = ""  # expected sha256 of cb-probe.zip (fail-closed)
    region: str = "cn-hangzhou"
    vswitch_id: str = ""
    security_group_id: str = ""
    ram_role: str = ""  # instance RAM role granting oss:PutObject
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
    health_check: Callable[[str], bool] | None = None  # (probe_url) -> ready?
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.monotonic
    instance_id: str | None = field(default=None, init=False)
    probe_url: str | None = field(default=None, init=False)
    token: str = field(default="", init=False)  # bearer token for the probe HTTP surface

    def provision(self) -> str:
        """Create the ECI, wait until Running + /health green; return probe_url.
        Raises CarrierError on timeout (never silently falls back)."""
        import secrets

        # Per-probe bearer token: the probe server (0.0.0.0, public IP) requires it
        # on /run-job and /job so a stranger can't drive it or read job results.
        self.token = secrets.token_urlsafe(32)
        req = self._build_create_request()
        self.instance_id = self.sdk.create_container_group(req)
        deadline = self.now() + self.config.ready_timeout_s
        health = self.health_check or self._default_health
        while self.now() < deadline:
            desc = self.sdk.describe_container_group(self.instance_id)
            status = str(desc.get("status") or "")
            ip = str(desc.get("public_ip") or "")
            if status == "Running" and ip:
                url = f"http://{ip}:{self.config.port}"
                if health(url):
                    self.probe_url = url
                    return url
            self.sleep(self.config.poll_interval_s)
        self.teardown()  # don't leak a half-booted instance
        raise CarrierError(f"ECI probe {self.instance_id} not healthy within {self.config.ready_timeout_s}s")

    def teardown(self) -> None:
        """Reap the ECI. Idempotent + best-effort (called from a finally)."""
        iid, self.instance_id = self.instance_id, None
        self.probe_url = None
        if iid is None:
            return
        try:
            self.sdk.delete_container_group(iid)
        except Exception:  # noqa: BLE001 — teardown must never raise out of finally
            pass

    def _build_create_request(self) -> dict[str, Any]:
        c = self.config
        code_bucket, code_key = _split_oss_uri(c.oss_code_uri)
        # Real OSS fetch from inside the ECI, authenticated by the instance RAM
        # role via the ECS-RAM-role credential provider (no static creds).
        fetch = (
            "import os,oss2;"
            "from oss2.credentials import EcsRamRoleCredentialsProvider;"
            "b=oss2.Bucket(oss2.ProviderAuthV4(EcsRamRoleCredentialsProvider()),"
            "'https://oss-'+os.environ['CB_PROBE_REGION']+'.aliyuncs.com',"
            "os.environ['CB_PROBE_CODE_BUCKET'],region=os.environ['CB_PROBE_REGION']);"
            "b.get_object_to_file(os.environ['CB_PROBE_CODE_KEY'],'/tmp/probe.zip')"
        )
        # Verify the fetched code against the expected sha256 BEFORE extracting or
        # running it. Fail-closed: an unset/mismatched hash aborts (set -e), so a
        # tampered or swapped OSS object can never be executed on the ECI (which
        # holds the instance RAM role). Closes the "fetch == RCE" hole.
        verify = (
            "import hashlib,os,sys;"
            "d=open('/tmp/probe.zip','rb').read();"
            "w=os.environ.get('CB_PROBE_CODE_SHA256','');"
            "sys.exit(0 if w and hashlib.sha256(d).hexdigest()==w else 1)"
        )
        bootstrap = (
            "set -e; pip install oss2 requests >/dev/null; "
            f'python -c "{fetch}"; '
            f'python -c "{verify}"; '
            "cd /tmp && python -m zipfile -e probe.zip probe && cd probe && "
            f"PORT={c.port} python -m clousight_bench.domains.agent_runtime.probe.server"
        )
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
                    "port": [{"port": c.port, "protocol": "TCP"}],
                    "command": ["/bin/sh", "-c", bootstrap],
                    "environment_var": [
                        {"key": "PORT", "value": str(c.port)},
                        {"key": "CB_PROBE_REGION", "value": c.region},
                        {"key": "CB_PROBE_CODE_BUCKET", "value": code_bucket},
                        {"key": "CB_PROBE_CODE_KEY", "value": code_key},
                        {"key": "CB_PROBE_CODE_SHA256", "value": c.code_sha256},
                        {"key": "CB_PROBE_TOKEN", "value": self.token},
                    ],
                }
            ],
        }

    def _default_health(self, probe_url: str) -> bool:
        import requests

        try:
            return requests.get(f"{probe_url}/health", timeout=5).status_code == 200
        except Exception:  # noqa: BLE001
            return False


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
                def __getattr__(self, _: str) -> None:  # type: ignore[override]
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
