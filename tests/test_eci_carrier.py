# tests/test_eci_carrier.py
import pytest

from clousight_bench.domains.agent_runtime.eci_carrier import (
    CarrierError,
    EciCarrierConfig,
    EciProbeCarrier,
)

PROBE_IMAGE = "registry-vpc.cn-hangzhou.aliyuncs.com/clousight-bench/cb-probe:abc123"
BUCKET = "my-bench-bucket"
CAMPAIGN_ID = "campaign-abc123"


class FakeEciSdk:
    """Records the create/describe/delete call sequence; scripts describe()."""

    def __init__(self, describe_script):
        self.script = list(describe_script)
        self.created_req = None
        self.deleted = []
        self._i = 0

    def create_container_group(self, req):
        self.created_req = req
        return "eci-123"

    def describe_container_group(self, instance_id):
        d = self.script[min(self._i, len(self.script) - 1)]
        self._i += 1
        return d

    def delete_container_group(self, instance_id):
        self.deleted.append(instance_id)


def _make_config(**kwargs):
    """Return an EciCarrierConfig with sensible test defaults."""
    defaults = dict(
        image=PROBE_IMAGE,
        bucket=BUCKET,
        campaign_id=CAMPAIGN_ID,
        region="cn-hangzhou",
        run_id="run-abcdef12",
    )
    defaults.update(kwargs)
    return EciCarrierConfig(**defaults)


# ---------------------------------------------------------------------------
# Create-request shape assertions
# ---------------------------------------------------------------------------


def test_create_request_has_no_eip_or_public_ip_fields():
    """No EIP / public-IP field must appear anywhere in the create request."""
    sdk = FakeEciSdk([{"status": "Running"}])
    carrier = EciProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    req = sdk.created_req

    # Flatten all keys recursively and check for EIP-related names
    def all_keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from all_keys(v)
        elif isinstance(obj, list):
            for item in obj:
                yield from all_keys(item)

    keys = set(all_keys(req))
    eip_keys = {
        k for k in keys if "eip" in k.lower() or "public_ip" in k.lower() or "internet_ip" in k.lower()
    }
    assert not eip_keys, f"Unexpected EIP/public-IP keys in create request: {eip_keys}"


def test_create_request_uses_prebuilt_probe_image():
    sdk = FakeEciSdk([{"status": "Running"}])
    carrier = EciProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    req = sdk.created_req
    assert req["container"][0]["image"] == PROBE_IMAGE


def test_create_request_has_no_command_override():
    """The prebuilt image bakes the probe + deps, so its ENTRYPOINT runs agent_loop
    with no `command` override and no runtime fetch (docker hub / github / pypi are
    throttled from cn-hangzhou)."""
    sdk = FakeEciSdk([{"status": "Running"}])
    carrier = EciProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    container = sdk.created_req["container"][0]
    assert "command" not in container


def test_provision_without_image_raises_clear_error():
    """An empty image (operator hasn't built + set eci_image) fails fast with an
    actionable message rather than launching a broken container group."""
    import pytest

    from clousight_bench.domains.agent_runtime.eci_carrier import CarrierError

    sdk = FakeEciSdk([{"status": "Running"}])
    carrier = EciProbeCarrier(
        sdk=sdk,
        config=_make_config(image=""),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    with pytest.raises(CarrierError, match="eci_image"):
        carrier.provision()


def test_create_request_env_vars_match_agent_loop_contract():
    """The four CB_PROBE_* env vars must exactly match agent_loop.main()'s reads."""
    sdk = FakeEciSdk([{"status": "Running"}])
    carrier = EciProbeCarrier(
        sdk=sdk,
        config=_make_config(bucket=BUCKET, campaign_id=CAMPAIGN_ID, region="cn-shenzhen"),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    req = sdk.created_req
    env = {e["key"]: e["value"] for e in req["container"][0]["environment_var"]}

    assert env["CB_PROBE_BUCKET"] == BUCKET
    assert env["CB_PROBE_REGION"] == "cn-shenzhen"
    assert env["CB_PROBE_CONTROL_PREFIX"] == CAMPAIGN_ID
    # Token is generated per provision; just verify the key exists and is non-empty
    assert "CB_PROBE_TOKEN" in env and env["CB_PROBE_TOKEN"]


def test_create_request_no_legacy_env_keys():
    """Old bootstrap env keys (CB_PROBE_CODE_BUCKET, CB_PROBE_CODE_KEY, etc.) must be gone."""
    sdk = FakeEciSdk([{"status": "Running"}])
    carrier = EciProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    req = sdk.created_req
    env_keys = {e["key"] for e in req["container"][0]["environment_var"]}
    legacy = {"CB_PROBE_CODE_BUCKET", "CB_PROBE_CODE_KEY", "CB_PROBE_CODE_SHA256", "PORT"}
    overlap = env_keys & legacy
    assert not overlap, f"Legacy env keys still present: {overlap}"


# ---------------------------------------------------------------------------
# Provision readiness gate
# ---------------------------------------------------------------------------


def test_provision_returns_campaign_id_when_running_and_ready():
    sdk = FakeEciSdk(
        [
            {"status": "Pending"},
            {"status": "Running"},
        ]
    )
    carrier = EciProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    result = carrier.provision()
    assert result == CAMPAIGN_ID
    assert carrier.control_prefix == CAMPAIGN_ID
    assert carrier.instance_id == "eci-123"


def test_provision_waits_until_both_running_and_oss_ready():
    """provision() must not return until status=Running AND ready_check() is True."""
    sdk = FakeEciSdk([{"status": "Running"}])
    calls = {"n": 0}

    def ready():
        calls["n"] += 1
        return calls["n"] >= 2  # green only on the 2nd call

    ticks = iter([0.0, 1.0, 2.0, 3.0])
    carrier = EciProbeCarrier(
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
    ticks = iter([0.0, 100.0, 200.0])
    sdk = FakeEciSdk([{"status": "Pending"}])
    carrier = EciProbeCarrier(
        sdk=sdk,
        config=_make_config(ready_timeout_s=150.0),
        ready_check=lambda: False,
        sleep=lambda s: None,
        now=lambda: next(ticks),
    )
    with pytest.raises(CarrierError):
        carrier.provision()
    assert sdk.deleted == ["eci-123"]  # half-booted instance reaped
    assert carrier.instance_id is None


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


def test_teardown_is_idempotent_and_best_effort():
    class Boom(FakeEciSdk):
        def delete_container_group(self, instance_id):
            raise RuntimeError("transient")

    sdk = Boom([{"status": "Running"}])
    carrier = EciProbeCarrier(
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
    sdk = FakeEciSdk([])
    carrier = EciProbeCarrier(
        sdk=sdk,
        config=_make_config(),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.teardown()  # no provision() first — should be a no-op
    assert sdk.deleted == []
    assert carrier.instance_id is None


# ---------------------------------------------------------------------------
# Structural / tag checks
# ---------------------------------------------------------------------------


def test_create_request_has_managed_tag_and_run_id_tag():
    sdk = FakeEciSdk([{"status": "Running"}])
    carrier = EciProbeCarrier(
        sdk=sdk,
        config=_make_config(run_id="run-abcdef12"),
        ready_check=lambda: True,
        sleep=lambda s: None,
        now=lambda: 0.0,
    )
    carrier.provision()
    req = sdk.created_req
    assert {"key": "clousight-bench:managed", "value": "true"} in req["tags"]
    assert any(t["key"] == "clousight-bench:run-id" for t in req["tags"])
