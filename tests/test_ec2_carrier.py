# tests/test_ec2_carrier.py
"""Tests for Ec2ProbeCarrier and Ec2CarrierConfig.

All tests are deterministic and offline: FakeEc2Sdk records calls and scripts
describe_instance() responses; sleep/now are injected so no real time passes.
"""

from __future__ import annotations

import base64

import pytest

from clousight_bench.domains.agent_runtime.aws.carrier import (
    CarrierError,
    Ec2CarrierConfig,
    Ec2ProbeCarrier,
)

STOCK_AMI = "ami-0abcdef1234567890"
BUCKET = "my-bench-bucket"
CAMPAIGN_ID = "campaign-abc123"
REGION = "us-east-1"


class FakeEc2Sdk:
    """Records run_instance/describe_instance/delete_instance calls; scripts describe()."""

    def __init__(self, describe_script: list[dict]):
        self.script = list(describe_script)
        self.run_req: dict | None = None
        self.deleted: list[str] = []
        self._i = 0

    def run_instance(self, req: dict) -> str:
        self.run_req = req
        return "i-ec2-123"

    def describe_instance(self, instance_id: str) -> dict:
        d = self.script[min(self._i, len(self.script) - 1)]
        self._i += 1
        return d

    def delete_instance(self, instance_id: str) -> None:
        self.deleted.append(instance_id)


def _make_config(**kwargs) -> Ec2CarrierConfig:
    """Return an Ec2CarrierConfig with sensible test defaults."""
    defaults = dict(
        image_id=STOCK_AMI,
        bucket=BUCKET,
        campaign_id=CAMPAIGN_ID,
        region=REGION,
        run_id="run-abcdef12",
        subnet_id="subnet-test-1",
        security_group_id="sg-test-1",
        iam_instance_profile="clousight-bench-probe",
    )
    defaults.update(kwargs)
    return Ec2CarrierConfig(**defaults)


# ---------------------------------------------------------------------------
# Run-request shape assertions
# ---------------------------------------------------------------------------


def test_run_request_has_image_id():
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    assert sdk.run_req["image_id"] == STOCK_AMI


def test_run_request_has_instance_type():
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(instance_type="t3.small"),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    assert sdk.run_req["instance_type"] == "t3.small"


def test_run_request_has_subnet_and_sg():
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(subnet_id="subnet-abc", security_group_id="sg-xyz"),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    req = sdk.run_req
    assert req["subnet_id"] == "subnet-abc"
    assert "sg-xyz" in req["security_group_ids"]


def test_run_request_has_iam_instance_profile():
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(iam_instance_profile="my-probe-role"),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    assert sdk.run_req["iam_instance_profile"] == "my-probe-role"


def test_run_request_has_no_public_ip():
    """associate_public_ip=False means no public IP — egress is via NAT."""
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    assert sdk.run_req["associate_public_ip"] is False


def test_run_request_has_name_tag_with_run_id_suffix():
    """Instance Name tag uses last 8 chars of run_id for easy identification."""
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(run_id="run-abcdef12"),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    # last 8 of "run-abcdef12" is "bcdef12" ... actually "abcdef12"[-8:] = "abcdef12"
    assert sdk.run_req["instance_name"] == "cb-probe-abcdef12"


# ---------------------------------------------------------------------------
# user_data / cloud-init content assertions
# ---------------------------------------------------------------------------


def _decode_user_data(sdk: FakeEc2Sdk) -> str:
    """Base64-decode the user_data from the run request."""
    return base64.b64decode(sdk.run_req["user_data"]).decode()


def test_user_data_exports_cb_probe_bucket():
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(bucket="test-bucket"),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert "CB_PROBE_BUCKET='test-bucket'" in script


def test_user_data_exports_cb_probe_region():
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(region="us-west-2"),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert "CB_PROBE_REGION='us-west-2'" in script


def test_user_data_exports_cb_probe_control_prefix():
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(campaign_id="cmp-xyz"),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert "CB_PROBE_CONTROL_PREFIX='cmp-xyz'" in script


def test_user_data_exports_cb_probe_token():
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    # Token is generated per provision; verify the export key exists and is non-empty
    assert "CB_PROBE_TOKEN='" in script
    # Extract token value from "export CB_PROBE_TOKEN='<value>'"
    token_line = next(line for line in script.splitlines() if "CB_PROBE_TOKEN" in line)
    token_val = token_line.split("'")[1]
    assert len(token_val) > 0
    assert carrier.token == token_val


def test_user_data_has_pip_install_code_spec():
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert "pip install" in script
    assert "clousight-bench[probe]" in script


def test_user_data_pip_install_uses_code_spec():
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(code_spec="clousight-bench[probe]==1.2.3"),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert "clousight-bench[probe]==1.2.3" in script


def test_user_data_ends_with_exec_probe_module():
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    # Runs the probe via `python3.11 -m ...agent_loop` (not the console script),
    # so it works regardless of where the entry point lands on PATH.
    last_line = [line for line in script.splitlines() if line.strip()][-1]
    assert last_line.startswith("exec python3.11 -m ")
    assert "clousight_bench.domains.agent_runtime.probe.agent_loop" in last_line


def test_user_data_installs_python311_interpreter():
    """Amazon Linux 2023 has Python 3.9; user-data must install >=3.10 first."""
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert "dnf install -y 'python3.11'" in script
    assert "python3.11 -m ensurepip" in script
    # every pip invocation goes through the 3.11 interpreter, never bare `pip`
    for line in script.splitlines():
        if "pip install" in line:
            assert line.startswith("python3.11 -m pip install")


def test_user_data_is_bash():
    """AWS cloud-init uses #!/bin/bash (not /bin/sh like Aliyun)."""
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert script.startswith("#!/bin/bash")


def test_user_data_installs_extra_deps_before_code_spec():
    """Dev-wheel path: probe extra's deps install from PyPI BEFORE the
    presigned wheel URL (a URL code_spec can't carry an [extra])."""
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(
            code_spec="https://s3-internal.example/w.whl?sig=x",
            extra_deps=["requests>=2.28", "boto3>=1.34"],
        ),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert "requests>=2.28" in script and "boto3>=1.34" in script
    # both extra deps are installed before the wheel URL
    wheel_at = script.index("https://s3-internal.example/w.whl")
    assert script.index("requests>=2.28") < wheel_at
    assert script.index("boto3>=1.34") < wheel_at
    # extra deps are single-quoted
    assert "pip install 'requests>=2.28'" in script


def test_user_data_with_pip_index_url():
    """When pip_index_url is set (e.g. CodeArtifact), -i flag is passed."""
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(
            pip_index_url="https://my.codeartifact.example/simple/",
            extra_deps=["requests>=2.28"],
        ),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert "-i 'https://my.codeartifact.example/simple/'" in script
    assert "pip install -i 'https://my.codeartifact.example/simple/' 'requests>=2.28'" in script


def test_user_data_without_pip_index_url_no_dash_i():
    """Default (empty pip_index_url) → no -i flag in pip install lines."""
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(pip_index_url=""),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    for line in script.splitlines():
        if "pip install" in line:
            assert "-i " not in line


def test_user_data_exports_idle_timeout():
    """The carrier passes its idle_timeout to the probe via env so a long
    whole-campaign sweep doesn't self-exit between data-plane jobs."""
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(idle_timeout_s=1800.0),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert "export CB_PROBE_IDLE_TIMEOUT='1800.0'" in script
    assert "export CB_PROBE_JOB_MAX_WAIT='900.0'" in script  # default per-job cap


def test_user_data_no_extra_deps_by_default():
    """Default (published-package) path emits a single pip install, no pre-steps."""
    sdk = FakeEc2Sdk([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert script.count("pip install") == 1


# ---------------------------------------------------------------------------
# Provision readiness gate
# ---------------------------------------------------------------------------


def test_provision_returns_campaign_id_when_running_and_ready():
    sdk = FakeEc2Sdk(
        [
            {"status": "pending"},
            {"status": "running"},
        ]
    )
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    result = carrier.provision()
    assert result == CAMPAIGN_ID
    assert carrier.control_prefix == CAMPAIGN_ID
    assert carrier.instance_id == "i-ec2-123"


def test_provision_waits_until_both_running_and_s3_ready():
    """provision() must not return until status=running AND ready_check() is True."""
    sdk = FakeEc2Sdk([{"status": "running"}])
    calls: dict[str, int] = {"n": 0}

    def ready() -> bool:
        calls["n"] += 1
        return calls["n"] >= 2  # green only on the 2nd call

    ticks = iter([0.0, 1.0, 2.0, 3.0])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=ready,
        sleep=lambda s: None,
        now=lambda: next(ticks),
    )
    result = carrier.provision()
    assert result == CAMPAIGN_ID
    assert calls["n"] == 2


def test_provision_times_out_reaps_and_raises():
    """Timeout → teardown called, instance reaped, CarrierError raised."""
    ticks = iter([0.0, 100.0, 200.0, 400.0])
    sdk = FakeEc2Sdk([{"status": "pending"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(ready_timeout_s=150.0),
        ready_check=lambda: False,
        sleep=lambda s: None,
        now=lambda: next(ticks),
    )
    with pytest.raises(CarrierError):
        carrier.provision()
    assert sdk.deleted == ["i-ec2-123"]  # half-booted instance reaped
    assert carrier.instance_id is None


def test_provision_empty_image_id_raises_before_run():
    """Empty image_id → CarrierError with 'ec2_image_id' in message, no SDK call."""
    sdk = FakeEc2Sdk([])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(image_id=""),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    with pytest.raises(CarrierError, match="ec2_image_id"):
        carrier.provision()
    # No run_instance call should have been made
    assert sdk.run_req is None


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


def test_teardown_is_idempotent_and_best_effort():
    class Boom(FakeEc2Sdk):
        def delete_instance(self, instance_id: str) -> None:
            raise RuntimeError("transient")

    sdk = Boom([{"status": "running"}])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    carrier.teardown()  # swallows the RuntimeError
    carrier.teardown()  # second call is a no-op
    assert carrier.instance_id is None
    assert carrier.control_prefix is None


def test_teardown_before_provision_is_noop():
    """teardown() before provision() (finally-block scenario) must not call delete
    and must not raise."""
    sdk = FakeEc2Sdk([])
    carrier = Ec2ProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.teardown()  # no provision() first — should be a no-op
    assert sdk.deleted == []
    assert carrier.instance_id is None
