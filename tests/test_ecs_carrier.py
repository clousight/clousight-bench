# tests/test_ecs_carrier.py
"""Tests for EcsProbeCarrier and EcsCarrierConfig.

All tests are deterministic and offline: FakeEcsSdk records calls and scripts
describe_instance() responses; sleep/now are injected so no real time passes.
"""

from __future__ import annotations

import base64

import pytest

from clousight_bench.domains.agent_runtime.ecs_carrier import (
    CarrierError,
    EcsCarrierConfig,
    EcsProbeCarrier,
)

STOCK_IMAGE = "aliyun_3_x64_20G_alibase_20240819.vhd"
BUCKET = "my-bench-bucket"
CAMPAIGN_ID = "campaign-abc123"
REGION = "cn-hangzhou"


class FakeEcsSdk:
    """Records run_instance/describe_instance/delete_instance calls; scripts describe()."""

    def __init__(self, describe_script: list[dict]):
        self.script = list(describe_script)
        self.run_req: dict | None = None
        self.deleted: list[str] = []
        self._i = 0

    def run_instance(self, req: dict) -> str:
        self.run_req = req
        return "i-ecs-123"

    def describe_instance(self, instance_id: str) -> dict:
        d = self.script[min(self._i, len(self.script) - 1)]
        self._i += 1
        return d

    def delete_instance(self, instance_id: str) -> None:
        self.deleted.append(instance_id)


def _make_config(**kwargs) -> EcsCarrierConfig:
    """Return an EcsCarrierConfig with sensible test defaults."""
    defaults = dict(
        image_id=STOCK_IMAGE,
        bucket=BUCKET,
        campaign_id=CAMPAIGN_ID,
        region=REGION,
        run_id="run-abcdef12",
        vswitch_id="vsw-test-1",
        security_group_id="sg-test-1",
        ram_role="clousight-bench-probe",
    )
    defaults.update(kwargs)
    return EcsCarrierConfig(**defaults)


# ---------------------------------------------------------------------------
# Run-request shape assertions
# ---------------------------------------------------------------------------


def test_run_request_has_image_id():
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    assert sdk.run_req["image_id"] == STOCK_IMAGE


def test_run_request_has_instance_type():
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
        sdk=sdk,
        config=_make_config(instance_type="ecs.e-c1m2.large"),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    assert sdk.run_req["instance_type"] == "ecs.e-c1m2.large"


def test_run_request_has_vswitch_and_sg():
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
        sdk=sdk,
        config=_make_config(vswitch_id="vsw-abc", security_group_id="sg-xyz"),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    req = sdk.run_req
    assert req["v_switch_id"] == "vsw-abc"
    assert req["security_group_id"] == "sg-xyz"


def test_run_request_has_ram_role():
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
        sdk=sdk,
        config=_make_config(ram_role="my-probe-role"),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    assert sdk.run_req["ram_role_name"] == "my-probe-role"


def test_run_request_has_no_public_ip_internet_max_bandwidth_zero():
    """internet_max_bandwidth_out == 0 means no public EIP — egress is via NAT."""
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    assert sdk.run_req["internet_max_bandwidth_out"] == 0


# ---------------------------------------------------------------------------
# user_data / cloud-init content assertions
# ---------------------------------------------------------------------------


def _decode_user_data(sdk: FakeEcsSdk) -> str:
    """Base64-decode the user_data from the run request."""
    return base64.b64decode(sdk.run_req["user_data"]).decode()


def test_user_data_exports_cb_probe_bucket():
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
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
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
        sdk=sdk,
        config=_make_config(region="cn-shenzhen"),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert "CB_PROBE_REGION='cn-shenzhen'" in script


def test_user_data_exports_cb_probe_control_prefix():
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
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
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
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


def test_user_data_has_pip_install_with_aliyun_mirror():
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert "pip install" in script
    assert "mirrors.cloud.aliyuncs.com/pypi" in script


def test_user_data_pip_install_uses_code_spec():
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
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
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
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
    """Stock Aliyun Linux 3 has Python 3.6; user-data must install >=3.10 first."""
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert "yum install -y 'python3.11'" in script
    assert "python3.11 -m ensurepip" in script
    # every pip invocation goes through the 3.11 interpreter, never bare `pip`
    for line in script.splitlines():
        if "pip install" in line:
            assert line.startswith("python3.11 -m pip install")


def test_user_data_is_posix_sh():
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert script.startswith("#!/bin/sh")


def test_user_data_installs_extra_deps_before_code_spec():
    """Dev-wheel path: probe extra's deps install from the mirror BEFORE the
    presigned wheel URL (a URL code_spec can't carry an [extra])."""
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
        sdk=sdk,
        config=_make_config(
            code_spec="https://oss-internal.example/w.whl?sig=x",
            extra_deps=["requests>=2.28", "oss2>=2.18"],
        ),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    script = _decode_user_data(sdk)
    assert "requests>=2.28" in script and "oss2>=2.18" in script
    # both extra deps are installed before the wheel URL
    wheel_at = script.index("https://oss-internal.example/w.whl")
    assert script.index("requests>=2.28") < wheel_at
    assert script.index("oss2>=2.18") < wheel_at
    # extra deps are single-quoted and use the mirror
    assert "pip install -i 'https://mirrors.cloud.aliyuncs.com/pypi/simple/' 'requests>=2.28'" in script


def test_user_data_no_extra_deps_by_default():
    """Default (published-package) path emits a single pip install, no pre-steps."""
    sdk = FakeEcsSdk([{"status": "Running"}])
    carrier = EcsProbeCarrier(
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
    sdk = FakeEcsSdk(
        [
            {"status": "Pending"},
            {"status": "Running"},
        ]
    )
    carrier = EcsProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    result = carrier.provision()
    assert result == CAMPAIGN_ID
    assert carrier.control_prefix == CAMPAIGN_ID
    assert carrier.instance_id == "i-ecs-123"


def test_provision_waits_until_both_running_and_oss_ready():
    """provision() must not return until status=Running AND ready_check() is True."""
    sdk = FakeEcsSdk([{"status": "Running"}])
    calls: dict[str, int] = {"n": 0}

    def ready() -> bool:
        calls["n"] += 1
        return calls["n"] >= 2  # green only on the 2nd call

    ticks = iter([0.0, 1.0, 2.0, 3.0])
    carrier = EcsProbeCarrier(
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
    sdk = FakeEcsSdk([{"status": "Pending"}])
    carrier = EcsProbeCarrier(
        sdk=sdk,
        config=_make_config(ready_timeout_s=150.0),
        ready_check=lambda: False,
        sleep=lambda s: None,
        now=lambda: next(ticks),
    )
    with pytest.raises(CarrierError):
        carrier.provision()
    assert sdk.deleted == ["i-ecs-123"]  # half-booted instance reaped
    assert carrier.instance_id is None


def test_provision_empty_image_id_raises_before_run():
    """Empty image_id → CarrierError with 'ecs_image_id' in message, no SDK call."""
    sdk = FakeEcsSdk([])
    carrier = EcsProbeCarrier(
        sdk=sdk,
        config=_make_config(image_id=""),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    with pytest.raises(CarrierError, match="ecs_image_id"):
        carrier.provision()
    # No run_instance call should have been made
    assert sdk.run_req is None


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


def test_teardown_is_idempotent_and_best_effort():
    class Boom(FakeEcsSdk):
        def delete_instance(self, instance_id: str) -> None:
            raise RuntimeError("transient")

    sdk = Boom([{"status": "Running"}])
    carrier = EcsProbeCarrier(
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
    sdk = FakeEcsSdk([])
    carrier = EcsProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.teardown()  # no provision() first — should be a no-op
    assert sdk.deleted == []
    assert carrier.instance_id is None
