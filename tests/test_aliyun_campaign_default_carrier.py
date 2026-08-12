import clousight_bench.domains.agent_runtime.aliyun as al
from clousight_bench.domains.agent_runtime.eci_carrier import EciProbeCarrier


class _FakeSdk:
    def __init__(self, *a, **k):
        pass

    def create_container_group(self, req):
        self.req = req
        return "eci-1"

    def describe_container_group(self, i):
        return {"status": "Running", "public_ip": "9.9.9.9"}

    def delete_container_group(self, i):
        return None


def test_default_carrier_builds_real_carrier_with_ram_role_and_code_uri(monkeypatch):
    monkeypatch.setattr(al, "Eci20180808Sdk", _FakeSdk)
    probe = al._AliyunCampaignProbe()
    target = {
        "run_id": "run-xy",
        "oss_bucket": "bench-bkt",
        "region": "cn-hangzhou",
        "eci_probe_role": "clousight-bench-eci-probe",
        "eci_vswitch_id": "vsw-1",
        "eci_security_group_id": "sg-1",
    }
    carrier = probe._default_carrier(target, "clousight-bench/telemetry/run-xy/")
    assert isinstance(carrier, EciProbeCarrier)
    cfg = carrier.config
    assert cfg.ram_role == "clousight-bench-eci-probe"
    assert cfg.vswitch_id == "vsw-1" and cfg.security_group_id == "sg-1"
    assert cfg.region == "cn-hangzhou" and cfg.run_id == "run-xy"
    # OSS-mediated carrier: campaign_id + bucket drive the ECI env; no oss_code_uri.
    assert cfg.campaign_id == "run-xy" and cfg.bucket == "bench-bkt"
    # Readiness is an injected OSS-heartbeat check (not an HTTP /health call); with
    # the fake SDK reporting Running, provision returns the campaign_id/control prefix.
    carrier.ready_check = lambda: True
    assert carrier.provision() == "run-xy"


def test_default_oss_reads_bucket_and_region_from_target():
    from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client

    probe = al._AliyunCampaignProbe()
    oss = probe._default_oss({"oss_bucket": "b", "region": "cn-shanghai"})
    assert isinstance(oss, Oss2Client)
    assert oss._bucket_name == "b" and oss._region == "cn-shanghai"
