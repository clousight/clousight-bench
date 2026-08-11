# tests/test_eci_carrier.py
import pytest

from clousight_bench.domains.agent_runtime.eci_carrier import (
    CarrierError,
    EciCarrierConfig,
    EciProbeCarrier,
)


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


def test_provision_waits_for_running_and_health_then_returns_url():
    sdk = FakeEciSdk([
        {"status": "Pending", "public_ip": ""},
        {"status": "Running", "public_ip": "1.2.3.4"},
    ])
    carrier = EciProbeCarrier(
        sdk=sdk,
        config=EciCarrierConfig(port=9000, run_id="run-abcdef12",
                                oss_code_uri="oss://b/campaign-1/cb-probe.zip"),
        health_check=lambda url: True,     # /health green
        sleep=lambda s: None,              # no real waiting
        now=lambda: 0.0,                   # never times out on this short script
    )
    url = carrier.provision()
    assert url == "http://1.2.3.4:9000"
    assert carrier.probe_url == url and carrier.instance_id == "eci-123"
    # create request shape: mirrored py3.12 image, port, RAM-role slot, tags, bootstrap
    req = sdk.created_req
    assert "python:3.12" in req["container"][0]["image"]
    assert req["container"][0]["port"][0]["port"] == 9000
    assert {"key": "clousight-bench:managed", "value": "true"} in req["tags"]
    assert any(t["key"] == "clousight-bench:run-id" for t in req["tags"])
    assert "clousight_bench.domains.agent_runtime.probe.server" in req["container"][0]["command"][-1]


def test_provision_times_out_reaps_and_raises():
    # never reports Running → must time out, delete the instance, raise CarrierError
    ticks = iter([0.0, 100.0, 200.0])
    sdk = FakeEciSdk([{"status": "Pending", "public_ip": ""}])
    carrier = EciProbeCarrier(
        sdk=sdk, config=EciCarrierConfig(ready_timeout_s=150.0),
        health_check=lambda url: True, sleep=lambda s: None,
        now=lambda: next(ticks),
    )
    with pytest.raises(CarrierError):
        carrier.provision()
    assert sdk.deleted == ["eci-123"]          # half-booted instance reaped
    assert carrier.instance_id is None


def test_health_gate_blocks_url_until_green():
    sdk = FakeEciSdk([{"status": "Running", "public_ip": "1.2.3.4"}])
    calls = {"n": 0}
    def health(url):
        calls["n"] += 1
        return calls["n"] >= 2                  # green only on the 2nd check
    ticks = iter([0.0, 1.0, 2.0, 3.0])
    carrier = EciProbeCarrier(sdk=sdk, config=EciCarrierConfig(),
                              health_check=health, sleep=lambda s: None,
                              now=lambda: next(ticks))
    assert carrier.provision() == "http://1.2.3.4:9000"
    assert calls["n"] == 2


def test_teardown_is_idempotent_and_best_effort():
    class Boom(FakeEciSdk):
        def delete_container_group(self, instance_id):
            raise RuntimeError("transient")
    sdk = Boom([{"status": "Running", "public_ip": "1.2.3.4"}])
    carrier = EciProbeCarrier(sdk=sdk, config=EciCarrierConfig(),
                              health_check=lambda url: True, sleep=lambda s: None,
                              now=lambda: 0.0)
    carrier.provision()
    carrier.teardown()   # swallows the RuntimeError
    carrier.teardown()   # second call is a no-op
    assert carrier.instance_id is None


def test_teardown_before_provision_is_noop():
    """teardown() called before provision() (the finally-block scenario where
    provision raised before create_container_group returned) must not call
    delete_container_group and must not raise."""
    sdk = FakeEciSdk([])   # no describe script needed — teardown exits early
    carrier = EciProbeCarrier(sdk=sdk, config=EciCarrierConfig(),
                              health_check=lambda url: True, sleep=lambda s: None,
                              now=lambda: 0.0)
    carrier.teardown()   # no provision() called first — should be a no-op
    assert sdk.deleted == []          # delete_container_group never called
    assert carrier.instance_id is None
