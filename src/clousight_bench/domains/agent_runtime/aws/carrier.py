from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any, ClassVar

from clousight_bench.domains.agent_runtime.carrier_base import BaseProbeCarrier, CarrierError

__all__ = [
    "CarrierError",
    "Ec2CarrierConfig",
    "Ec2ProbeCarrier",
    "Boto3Ec2Sdk",
]


@dataclass
class Ec2CarrierConfig:
    """Everything the RunInstances request needs. Use a stock Amazon Linux 2023
    AMI — no private image or registry required.

    The EC2 instance boots a stock Amazon Linux 2023 AMI, cloud-init installs
    the published ``clousight-bench[probe]`` package from PyPI (via NAT egress),
    then runs the probe loop which reads the CB_PROBE_* env vars and starts the
    S3-mediated agent loop.  Only the AgentRun platform endpoint needs NAT
    egress; everything else is VPC-internal.
    """

    # Required: a STOCK Amazon Linux 2023 (or compatible) AMI id in the target
    # region.  Find one via the EC2 AMI console or aws ssm get-parameters-by-path
    # --path /aws/service/ami-amazon-linux-latest.
    # Set via target 'ec2_image_id' — empty → provision() raises a clear error.
    image_id: str = ""

    # Small EC2 instance type (2 vCPU / 2 GiB, burstable economy class).
    instance_type: str = "t3.small"

    # S3 control-plane fields (required for S3-mediated channel)
    bucket: str = ""  # S3 bucket name → CB_PROBE_BUCKET
    campaign_id: str = ""  # per-campaign id → CB_PROBE_CONTROL_PREFIX
    region: str = "us-east-1"

    # VPC networking — the instance gets NO public IP; all traffic via NAT gateway.
    subnet_id: str = ""
    security_group_id: str = ""

    # IAM instance profile granting s3:PutObject / s3:GetObject to the probe.
    # Specify the instance profile name or ARN.
    iam_instance_profile: str = ""

    # pip install target.  Released: "clousight-bench[probe]==<ver>".
    # Dev: a presigned S3 wheel URL.  Goes verbatim into the cloud-init script.
    code_spec: str = "clousight-bench[probe]"

    # Extra requirement specs pip-installed from PyPI BEFORE code_spec.
    # The dev-wheel path uses this: a presigned wheel URL code_spec can't carry
    # an [extra], so the probe extra's own deps are installed separately.
    extra_deps: list[str] = field(default_factory=list)

    # PyPI index URL.  Empty string = default PyPI (https://pypi.org/simple/) via
    # NAT egress.  AWS has no in-VPC mirror by default; set to a private CodeArtifact
    # or Nexus URL if available.
    pip_index_url: str = ""

    # Amazon Linux 2023 ships Python 3.9, but clousight-bench requires >=3.10 —
    # so cloud-init dnf-installs this interpreter package and runs everything
    # through `python3.11 -m`.  AL2023's dnf repo provides python3.11.
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
    # S3ProbeClient timeout so a slow AgentRuntime doesn't trip the probe's own
    # 300s default before the control plane would give up. Default 900s.
    job_max_wait_s: float = 900.0
    run_id: str | None = None


class Ec2ProbeCarrier(BaseProbeCarrier):
    """Ephemeral EC2 instance that runs the S3-mediated probe loop.

    Instead of launching a container (which requires a private image), this
    carrier launches a stock EC2 instance whose cloud-init user-data installs
    and runs the public ``clousight-bench[probe]`` package. The provision /
    teardown / readiness lifecycle is inherited from ``BaseProbeCarrier``; this
    class only supplies the EC2-specific run request, cloud-init and S3 client.
    """

    _RUNNING_STATUS: ClassVar[str] = "running"
    _KIND: ClassVar[str] = "EC2"

    def _missing_image_error(self) -> str:
        return (
            "no EC2 AMI configured: find a stock Amazon Linux 2023 AMI id for "
            "your region and set target 'ec2_image_id' to that id."
        )

    def _build_blob_client(self) -> Any:
        # S3Client: default credential chain (the control plane does not run on an
        # EC2 instance role). Lazy import so code that skips S3 doesn't need boto3
        # at import time.
        from clousight_bench.domains.agent_runtime.probe.s3_client import S3Client

        return S3Client(bucket=self.config.bucket, region=self.config.region)

    def _build_run_request(self) -> dict[str, Any]:
        """Build the EC2 RunInstances request dict.

        The instance gets no public IP (associate_public_ip=False): all external
        egress is via the VPC NAT gateway, which is provisioned separately and
        only when needed.  The cloud-init user-data installs the probe package
        from PyPI via NAT and runs the probe loop.
        """
        c = self.config
        user_data = self._build_user_data()
        req: dict[str, Any] = {
            "image_id": c.image_id,
            "instance_type": c.instance_type,
            "subnet_id": c.subnet_id,
            "security_group_ids": [c.security_group_id],
            "associate_public_ip": False,
            "instance_name": f"cb-probe-{(c.run_id or 'adhoc')[-8:]}",
            "user_data": user_data,
        }
        if c.iam_instance_profile:
            req["iam_instance_profile"] = c.iam_instance_profile
        return req

    def _build_user_data(self) -> str:
        """Return base64-encoded cloud-init user-data that installs + starts the probe.

        Single-quotes every interpolated value for bash safety, including
        code_spec: pip parses ``'clousight-bench[probe]'`` and ``'https://…wheel'``
        identically to the unquoted forms, so quoting costs nothing while it
        neutralises shell globbing (``[probe]``) and metacharacters (a presigned
        S3 URL's ``&``/``?``/``;``).
        """
        c = self.config
        py = c.python_pkg  # e.g. "python3.11" — command name matches the dnf pkg name
        lines = [
            "#!/bin/bash",
            "set -e",
            f"export CB_PROBE_BUCKET='{c.bucket}'",
            f"export CB_PROBE_REGION='{c.region}'",
            f"export CB_PROBE_CONTROL_PREFIX='{c.campaign_id}'",
            f"export CB_PROBE_TOKEN='{self.token}'",
            f"export CB_PROBE_IDLE_TIMEOUT='{c.idle_timeout_s}'",
            f"export CB_PROBE_JOB_MAX_WAIT='{c.job_max_wait_s}'",
            # Amazon Linux 2023 ships Python 3.9; install a >=3.10 interpreter and
            # bootstrap its pip, then use `python -m pip` throughout.
            f"dnf install -y '{py}'",
            f"{py} -m ensurepip",
        ]
        # Dev-wheel path: install the probe extra's deps from PyPI first,
        # since a presigned wheel URL code_spec can't carry an [extra].
        if c.pip_index_url:
            lines += [f"{py} -m pip install -i '{c.pip_index_url}' '{dep}'" for dep in c.extra_deps]
            lines.append(f"{py} -m pip install -i '{c.pip_index_url}' '{c.code_spec}'")
        else:
            lines += [f"{py} -m pip install '{dep}'" for dep in c.extra_deps]
            lines.append(f"{py} -m pip install '{c.code_spec}'")
        # Run via `-m` (agent_loop has a __main__) so we don't depend on the
        # console-script landing on PATH for this interpreter.
        lines.append(f"exec {py} -m clousight_bench.domains.agent_runtime.probe.agent_loop")
        script = "\n".join(lines) + "\n"
        return base64.b64encode(script.encode()).decode()


class Boto3Ec2Sdk:
    """Real ComputeSdk over boto3 (live path).

    Maps the carrier's plain-dict run request onto EC2 API calls and normalizes
    describe to the {"status"} contract the carrier expects.  The boto3 client
    is injectable so the mapping is unit-tested with a fake client (no network);
    production builds a real client from the default credential chain
    (env / ~/.aws / instance role).

    Lazy-import strategy: boto3 is imported on first use inside _cli(). In the
    test path the injected fake client means _cli() never touches boto3, so the
    package need not be installed in the test environment.
    """

    def __init__(self, region: str = "us-east-1", *, client: Any | None = None) -> None:
        self._region = region
        self._client = client

    def _cli(self) -> Any:
        if self._client is None:
            import boto3  # local import — never at module level

            self._client = boto3.client("ec2", region_name=self._region)
        return self._client

    def run_instance(self, req: dict[str, Any]) -> str:
        """Call RunInstances and return the new InstanceId."""
        network_interface: dict[str, Any] = {
            "DeviceIndex": 0,
            "AssociatePublicIpAddress": bool(req.get("associate_public_ip", False)),
        }
        if req.get("subnet_id"):
            network_interface["SubnetId"] = req["subnet_id"]
        if req.get("security_group_ids"):
            network_interface["Groups"] = list(req["security_group_ids"])

        kwargs: dict[str, Any] = {
            "ImageId": req["image_id"],
            "InstanceType": req["instance_type"],
            "MinCount": 1,
            "MaxCount": 1,
            "NetworkInterfaces": [network_interface],
            "TagSpecifications": [
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": req.get("instance_name", "")}],
                }
            ],
        }
        if req.get("user_data"):
            kwargs["UserData"] = req["user_data"]
        iam_profile = req.get("iam_instance_profile")
        if iam_profile:
            # Accept either a name or an ARN
            if iam_profile.startswith("arn:"):
                kwargs["IamInstanceProfile"] = {"Arn": iam_profile}
            else:
                kwargs["IamInstanceProfile"] = {"Name": iam_profile}

        resp = self._cli().run_instances(**kwargs)
        return str(resp["Instances"][0]["InstanceId"])

    def describe_instance(self, instance_id: str) -> dict[str, Any]:
        """Return {"status": <State.Name>} for the given instance."""
        resp = self._cli().describe_instances(InstanceIds=[instance_id])
        reservations = resp.get("Reservations") or []
        if not reservations:
            return {"status": ""}
        instances = reservations[0].get("Instances") or []
        if not instances:
            return {"status": ""}
        state = instances[0].get("State") or {}
        return {"status": str(state.get("Name") or "")}

    def delete_instance(self, instance_id: str) -> None:
        """Terminate the given instance."""
        self._cli().terminate_instances(InstanceIds=[instance_id])
