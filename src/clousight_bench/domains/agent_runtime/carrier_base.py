"""BaseProbeCarrier: the provision/teardown lifecycle every cloud probe carrier shares.

An in-region probe carrier launches a stock-OS instance whose cloud-init installs
and runs the public ``clousight-bench[probe]`` package, then talks to the control
plane over the object-store-mediated ``OssChannel`` / agent_loop contract. The
poll-until-running-and-heartbeat loop, the best-effort teardown and the
OSS-channel readiness wiring are identical across clouds; only the run-request
shape, the cloud-init user-data and the blob client differ. Cloud subclasses
(``EcsProbeCarrier`` over ECS, ``Ec2ProbeCarrier`` over EC2) implement those hooks;
everything else lives here once.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol


class CarrierError(RuntimeError):
    """Probe carrier provisioning failed (no silent fallback)."""


class ComputeSdk(Protocol):
    """The three compute operations a probe carrier needs. Cloud SDKs
    (Ecs20140526Sdk over alibabacloud_ecs20140526, Boto3Ec2Sdk over boto3)
    supply the real impl; tests inject fakes. Account-free seam."""

    def run_instance(self, req: dict[str, Any]) -> str: ...  # -> instance_id

    def describe_instance(self, instance_id: str) -> dict[str, Any]: ...  # {"status"}

    def delete_instance(self, instance_id: str) -> None: ...


@dataclass
class BaseProbeCarrier:
    """Shared lifecycle for an ephemeral in-region probe instance.

    Subclasses supply the run-request shape (``_build_run_request``), the
    cloud-init user-data (``_build_user_data``), the readiness blob client
    (``_build_blob_client``) and the missing-image error text
    (``_missing_image_error``), plus the ``_RUNNING_STATUS`` / ``_KIND`` class
    vars; the provision poll loop, teardown and readiness wiring are inherited.
    """

    sdk: ComputeSdk
    config: Any
    # ready_check() -> True once the object-store heartbeat key exists.
    # Defaults to None; provision() builds the default from config if not supplied.
    ready_check: Callable[[], bool] | None = None
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.monotonic
    instance_id: str | None = field(default=None, init=False)
    control_prefix: str | None = field(default=None, init=False)
    token: str = field(default="", init=False)  # bearer token injected via cloud-init

    # describe_instance() status string that means "running" ("Running" on ECS,
    # "running" on EC2).
    _RUNNING_STATUS: ClassVar[str] = "running"
    # Label used in the not-ready error ("ECS" / "EC2").
    _KIND: ClassVar[str] = "probe"

    def provision(self) -> str:
        """Create the instance, wait until running AND the heartbeat fires.

        Returns the control prefix (campaign_id) that both the carrier and the
        probe loop use to scope their object-store channel. Raises CarrierError on
        timeout (never silently falls back — on timeout, tears down + raises).
        """
        if not self.config.image_id:
            raise CarrierError(self._missing_image_error())

        self.token = secrets.token_urlsafe(32)
        req = self._build_run_request()
        self.instance_id = self.sdk.run_instance(req)
        deadline = self.now() + self.config.ready_timeout_s
        ready = self.ready_check if self.ready_check is not None else self._default_ready_check()
        while self.now() < deadline:
            desc = self.sdk.describe_instance(self.instance_id)
            status = str(desc.get("status") or "")
            if status == self._RUNNING_STATUS and ready():
                self.control_prefix = self.config.campaign_id
                return self.config.campaign_id
            self.sleep(self.config.poll_interval_s)
        self.teardown()  # don't leak a half-booted instance
        raise CarrierError(
            f"{self._KIND} probe {self.instance_id} not ready within {self.config.ready_timeout_s}s"
        )

    def teardown(self) -> None:
        """Reap the instance. Idempotent + best-effort (called from a finally)."""
        iid, self.instance_id = self.instance_id, None
        self.control_prefix = None
        if iid is None:
            return
        try:
            self.sdk.delete_instance(iid)
        except Exception:  # noqa: BLE001 — teardown must never raise out of finally
            pass

    def _default_ready_check(self) -> Callable[[], bool]:
        """Object-store-heartbeat readiness: an OssChannel over the cloud blob client.

        The control plane does NOT run on an instance role, so the blob client
        uses the default credential chain / public endpoint (only the probe
        instance itself uses the instance role). Lazy import so code paths that
        skip the object store don't need the SDK at import time.
        """
        from clousight_bench.domains.agent_runtime.probe.oss_channel import OssChannel

        channel = OssChannel(self._build_blob_client(), campaign_id=self.config.campaign_id)
        return channel.is_ready

    # ---- cloud-specific hooks ------------------------------------------------

    def _missing_image_error(self) -> str:
        raise NotImplementedError

    def _build_run_request(self) -> dict[str, Any]:
        raise NotImplementedError

    def _build_user_data(self) -> str:
        raise NotImplementedError

    def _build_blob_client(self) -> Any:
        raise NotImplementedError
