from __future__ import annotations

import base64
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol


def build_controller_user_data(
    *,
    bucket: str,
    region: str,
    campaign_id: str,
    results_dir: str = "/var/lib/cb/results",
    platform: str = "aliyun-agentrun",
    code_spec: str = "clousight-bench[probe,store]",
    python_pkg: str = "python3.11",
    pip_index_url: str = "https://mirrors.cloud.aliyuncs.com/pypi/simple/",
    extra_deps: Iterable[str] = (),
) -> str:
    """Base64 cloud-init user-data for the PROD controller instance.

    Mirrors the probe carrier's install pattern but installs the ``[probe,store]``
    extra (so the controller can write parquet sidecars in-cloud) and runs
    ``cb-controller`` (``core.controller_main``) instead of the probe loop. Env
    vars follow the ``CB_*`` names ``controller_main.build`` reads.
    """
    py = python_pkg
    lines = [
        "#!/bin/sh",
        "set -e",
        f"export CB_CAMPAIGN_ID='{campaign_id}'",
        f"export CB_OSS_BUCKET='{bucket}'",
        f"export CB_REGION='{region}'",
        f"export CB_RESULTS_DIR='{results_dir}'",
        f"export CB_PLATFORM='{platform}'",
        f"yum install -y '{py}'",
        f"{py} -m ensurepip --upgrade",
    ]
    lines += [f"{py} -m pip install -i '{pip_index_url}' '{dep}'" for dep in extra_deps]
    lines.append(f"{py} -m pip install -i '{pip_index_url}' '{code_spec}'")
    lines.append(f"exec {py} -m clousight_bench.core.controller_main")
    script = "\n".join(lines) + "\n"
    return base64.b64encode(script.encode()).decode()


class CarrierError(RuntimeError):
    """Probe carrier provisioning failed (no silent fallback)."""


class EcsSdk(Protocol):
    """The three ECS operations the carrier needs. Ecs20140526Sdk supplies the
    real impl over alibabacloud_ecs20140526; tests inject FakeEcsSdk.
    Account-free seam."""

    def run_instance(self, req: dict[str, Any]) -> str: ...  # -> instance_id
    def describe_instance(self, instance_id: str) -> dict[str, Any]: ...  # {"status"}
    def delete_instance(self, instance_id: str) -> None: ...


@dataclass
class EcsCarrierConfig:
    """Everything the run-instance request needs. Use a stock Aliyun OS image —
    no private image or registry required.

    The ECS instance boots a stock Aliyun Linux image, cloud-init installs the
    published ``clousight-bench[probe]`` package from the Aliyun PyPI mirror
    (VPC-internal, no public egress for PyPI), then runs ``cb-probe`` which
    reads the CB_PROBE_* env vars and starts the OSS-mediated agent loop.
    Only the AgentRun platform endpoint needs NAT egress; everything else is
    VPC-internal.
    """

    # Required: a STOCK Aliyun Linux 3 (or Ubuntu) image id in the target region.
    # Find one via `aliyun ecs DescribeImages --OSType linux --ImageOwnerAlias system`.
    # Set via target 'ecs_image_id' — empty → provision() raises a clear error.
    image_id: str = ""

    # Small ECS instance type (2 vCPU / 4 GiB, burstable economy class).
    instance_type: str = "ecs.e-c1m2.large"

    # OSS control-plane fields (required for OSS-mediated channel)
    bucket: str = ""  # OSS bucket name → CB_PROBE_BUCKET
    campaign_id: str = ""  # per-campaign id → CB_PROBE_CONTROL_PREFIX
    region: str = "cn-hangzhou"

    # VPC networking — the instance gets NO EIP; all traffic via NAT gateway.
    vswitch_id: str = ""
    security_group_id: str = ""

    # Instance RAM role granting oss:PutObject / oss:GetObject to the probe.
    ram_role: str = ""

    # pip install target.  Released: "clousight-bench[probe]==<ver>".
    # Dev: a presigned OSS wheel URL.  Goes verbatim into the cloud-init script.
    code_spec: str = "clousight-bench[probe]"

    # Extra requirement specs pip-installed from the mirror BEFORE code_spec.
    # The dev-wheel path uses this: a presigned wheel URL can't carry an [extra],
    # so the probe extra's own deps (requests/oss2) are installed separately.
    extra_deps: list[str] = field(default_factory=list)

    # Aliyun VPC-internal PyPI mirror — no public egress needed.
    pip_index_url: str = "https://mirrors.cloud.aliyuncs.com/pypi/simple/"

    # Stock Aliyun Linux 3 ships Python 3.6 (and no `pip` on PATH), but
    # clousight-bench requires >=3.10 — so cloud-init yum-installs this
    # interpreter package and runs everything through `python3.11 -m`.
    python_pkg: str = "python3.11"

    ready_timeout_s: float = 300.0
    poll_interval_s: float = 5.0
    # How long the in-region probe stays alive with no dispatched job before it
    # self-exits (failsafe against a crashed control plane). Must exceed the
    # longest gap between data-plane jobs in a campaign — a full 25-task sweep has
    # control-plane stretches (T0.x provisioning) with no data-plane job, so the
    # control plane sets this to span the whole campaign. Default 1h.
    idle_timeout_s: float = 3600.0
    # Per-job execution cap on the probe side; should match the control plane's
    # OssProbeClient timeout so a slow AgentRuntime doesn't trip the probe's own
    # 300s default before the control plane would give up. Default 900s.
    job_max_wait_s: float = 900.0
    run_id: str | None = None


@dataclass
class EcsProbeCarrier:
    """Ephemeral ECS instance that runs the OSS-mediated probe loop.

    provision() / teardown() / ready_check / _default_ready_check /
    injectable sleep+now. The control channel is the OSS-mediated
    OssChannel / agent_loop contract shared by every probe carrier.

    Instead of launching a container (which requires a private image), this
    carrier launches a stock ECS instance whose cloud-init user-data installs
    and runs the public ``clousight-bench[probe]`` package.
    """

    sdk: EcsSdk
    config: EcsCarrierConfig
    # ready_check() -> True once the OSS heartbeat key exists.
    # Defaults to None; provision() builds the default from config if not supplied.
    ready_check: Callable[[], bool] | None = None
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.monotonic
    instance_id: str | None = field(default=None, init=False)
    control_prefix: str | None = field(default=None, init=False)
    token: str = field(default="", init=False)  # bearer token injected via cloud-init

    def provision(self) -> str:
        """Create the ECS instance, wait until Running AND the OSS heartbeat fires.

        Returns the control prefix (campaign_id) that both the carrier and the
        probe loop use to scope their OSS channel.  Raises CarrierError on timeout
        (never silently falls back — on timeout, tears down + raises).
        """
        import secrets

        if not self.config.image_id:
            raise CarrierError(
                "no ECS OS image configured: find a stock Aliyun Linux 3 image id for "
                "your region and set target 'ecs_image_id' to that id."
            )

        self.token = secrets.token_urlsafe(32)
        req = self._build_run_request()
        self.instance_id = self.sdk.run_instance(req)
        deadline = self.now() + self.config.ready_timeout_s
        ready = self.ready_check if self.ready_check is not None else self._default_ready_check()
        while self.now() < deadline:
            desc = self.sdk.describe_instance(self.instance_id)
            status = str(desc.get("status") or "")
            if status == "Running" and ready():
                self.control_prefix = self.config.campaign_id
                return self.config.campaign_id
            self.sleep(self.config.poll_interval_s)
        self.teardown()  # don't leak a half-booted instance
        raise CarrierError(f"ECS probe {self.instance_id} not ready within {self.config.ready_timeout_s}s")

    def teardown(self) -> None:
        """Reap the ECS instance. Idempotent + best-effort (called from a finally)."""
        iid, self.instance_id = self.instance_id, None
        self.control_prefix = None
        if iid is None:
            return
        try:
            self.sdk.delete_instance(iid)
        except Exception:  # noqa: BLE001 — teardown must never raise out of finally
            pass

    def _default_ready_check(self) -> Callable[[], bool]:
        """Build the default OSS-heartbeat readiness check from config.

        Constructs an OssChannel pointed at config.bucket / config.region /
        config.campaign_id and returns channel.is_ready as the callable.
        Uses Oss2Client (default credential chain, public endpoint) because the
        control plane does NOT run on an ECS instance role — only the probe ECS
        itself uses EcsRamRoleOssClient.
        Lazy import so production code that skips OSS doesn't need oss2 at
        import time.
        """
        from clousight_bench.domains.agent_runtime.probe.oss_channel import OssChannel
        from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client

        oss = Oss2Client(bucket=self.config.bucket, region=self.config.region)
        channel = OssChannel(oss, campaign_id=self.config.campaign_id)
        return channel.is_ready

    def _build_run_request(self) -> dict[str, Any]:
        """Build the ECS RunInstances request dict.

        The instance gets NO EIP (internet_max_bandwidth_out=0): all external
        egress is via the VPC NAT gateway, which is provisioned separately and
        only when needed.  The cloud-init user-data installs the probe package
        from the Aliyun VPC-internal PyPI mirror and runs ``cb-probe``.
        """
        c = self.config
        user_data = self._build_user_data()
        return {
            "region_id": c.region,
            "image_id": c.image_id,
            "instance_type": c.instance_type,
            "v_switch_id": c.vswitch_id,
            "security_group_id": c.security_group_id,
            "ram_role_name": c.ram_role,
            "instance_charge_type": "PostPaid",
            "amount": 1,
            "instance_name": f"cb-probe-{(c.run_id or 'adhoc')[-8:]}",
            # No public IP — egress is via the VPC NAT gateway only.
            "internet_max_bandwidth_out": 0,
            "user_data": user_data,
        }

    def _build_user_data(self) -> str:
        """Return base64-encoded cloud-init user-data that installs + starts the probe.

        Single-quotes every interpolated value for POSIX /bin/sh safety, including
        code_spec: pip parses ``'clousight-bench[probe]'`` and ``'https://…wheel'``
        identically to the unquoted forms, so quoting costs nothing while it
        neutralises shell globbing (``[probe]``) and metacharacters (a presigned
        OSS URL's ``&``/``?``/``;``).
        """
        c = self.config
        py = c.python_pkg  # e.g. "python3.11" — command name matches the yum pkg name
        lines = [
            "#!/bin/sh",
            "set -e",
            f"export CB_PROBE_BUCKET='{c.bucket}'",
            f"export CB_PROBE_REGION='{c.region}'",
            f"export CB_PROBE_CONTROL_PREFIX='{c.campaign_id}'",
            f"export CB_PROBE_TOKEN='{self.token}'",
            f"export CB_PROBE_IDLE_TIMEOUT='{c.idle_timeout_s}'",
            f"export CB_PROBE_JOB_MAX_WAIT='{c.job_max_wait_s}'",
            # Stock Aliyun Linux 3 has Python 3.6 and no `pip`; install a >=3.10
            # interpreter and bootstrap its pip, then use `python -m pip` throughout.
            f"yum install -y '{py}'",
            f"{py} -m ensurepip --upgrade",
        ]
        # Dev-wheel path: install the probe extra's deps from the mirror first,
        # since a presigned wheel URL code_spec can't carry an [extra].
        lines += [f"{py} -m pip install -i '{c.pip_index_url}' '{dep}'" for dep in c.extra_deps]
        lines.append(f"{py} -m pip install -i '{c.pip_index_url}' '{c.code_spec}'")
        # Run via `-m` (agent_loop has a __main__) so we don't depend on the
        # console-script landing on PATH for this interpreter.
        lines.append(f"exec {py} -m clousight_bench.domains.agent_runtime.probe.agent_loop")
        script = "\n".join(lines) + "\n"
        return base64.b64encode(script.encode()).decode()


class Ecs20140526Sdk:
    """Real EcsSdk over alibabacloud_ecs20140526 (live path).

    Maps the carrier's plain-dict run request onto ECS request models and
    normalizes describe to the {"status"} contract the carrier expects.  The
    SDK client is injectable so the mapping is unit-tested with a fake client
    (no network); production builds a real Client from the default credential
    chain (env / RAM-user AK / instance role).

    Lazy-import strategy: both the ECS Client and the models module are imported
    on first use inside methods.  In the test path the injected fake client means
    _cli() never touches alibabacloud_ecs20140526, and the model objects are built
    via _make (types.SimpleNamespace) so the package need not be installed.
    In the live path _make delegates to real SDK model constructors.
    """

    def __init__(self, region: str = "cn-hangzhou", *, client: Any | None = None) -> None:
        self._region = region
        self._client = client

    def _cli(self) -> Any:
        if self._client is None:
            from alibabacloud_credentials.client import Client as CredClient
            from alibabacloud_ecs20140526.client import Client as EcsClient
            from alibabacloud_tea_openapi import models as open_api_models

            cfg = open_api_models.Config(credential=CredClient())
            cfg.endpoint = f"ecs.{self._region}.aliyuncs.com"
            self._client = EcsClient(cfg)
        return self._client

    @staticmethod
    def _make(model_cls: Any, **kwargs: Any) -> Any:
        """Construct a model object.

        When the SDK package is present (live path), model_cls is a real SDK
        model class.  When absent (test path, fake client injected), model_cls
        is None (from _NoneProxy) and we return a types.SimpleNamespace stand-in.
        """
        import types as _types

        if model_cls is None:
            return _types.SimpleNamespace(**kwargs)
        return model_cls(**kwargs)

    def _models(self) -> Any:
        """Return the alibabacloud_ecs20140526.models module, or a _NoneProxy
        whose attributes are None (test path where the package is absent and a
        fake client has been injected)."""
        try:
            from alibabacloud_ecs20140526 import models as m

            return m
        except ImportError:

            class _NoneProxy:
                def __getattr__(self, _: str) -> None:
                    return None

            return _NoneProxy()

    def run_instance(self, req: dict[str, Any]) -> str:
        m = self._models()
        request = self._make(
            m.RunInstancesRequest,
            region_id=req["region_id"],
            image_id=req["image_id"],
            instance_type=req["instance_type"],
            v_switch_id=req.get("v_switch_id") or None,
            security_group_id=req.get("security_group_id") or None,
            ram_role_name=req.get("ram_role_name") or None,
            internet_max_bandwidth_out=int(req.get("internet_max_bandwidth_out", 0)),
            instance_charge_type=req.get("instance_charge_type", "PostPaid"),
            amount=int(req.get("amount", 1)),
            instance_name=req.get("instance_name") or None,
            user_data=req.get("user_data") or None,
        )
        resp = self._cli().run_instances(request)
        return str(resp.body.instance_id_sets.instance_id_set[0])

    def describe_instance(self, instance_id: str) -> dict[str, Any]:
        import json as _json

        m = self._models()
        request = self._make(
            m.DescribeInstancesRequest,
            region_id=self._region,
            instance_ids=_json.dumps([instance_id]),
        )
        resp = self._cli().describe_instances(request)
        instances = getattr(resp.body, "instances", None)
        if instances is not None:
            instance_list = getattr(instances, "instance", None) or []
        else:
            instance_list = []
        if not instance_list:
            return {"status": ""}
        inst = instance_list[0]
        return {"status": str(getattr(inst, "status", "") or "")}

    def delete_instance(self, instance_id: str) -> None:
        m = self._models()
        request = self._make(
            m.DeleteInstancesRequest,
            region_id=self._region,
            instance_id=[instance_id],
            force=True,
        )
        self._cli().delete_instances(request)
