import clousight_bench.domains.agent_runtime.aliyun as al
from clousight_bench.domains.agent_runtime.ecs_carrier import EcsProbeCarrier


class _FakeSdk:
    def __init__(self, *a, **k):
        pass

    def run_instance(self, req):
        self.req = req
        return "i-1"

    def describe_instance(self, i):
        return {"status": "Running"}

    def delete_instance(self, i):
        return None


def test_default_carrier_builds_real_ecs_carrier_with_ram_role_and_image(monkeypatch):
    monkeypatch.setattr(al, "Ecs20140526Sdk", _FakeSdk)
    probe = al._AliyunCampaignProbe()
    target = {
        "run_id": "run-xy",
        "oss_bucket": "bench-bkt",
        "region": "cn-hangzhou",
        "eci_probe_role": "clousight-bench-eci-probe",
        "eci_vswitch_id": "vsw-1",
        "eci_security_group_id": "sg-1",
        "ecs_image_id": "aliyun_3_x64_20G_alibase_image",
        "ecs_instance_type": "ecs.e-c1m2.large",
    }
    carrier = probe._default_carrier(target, "clousight-bench/telemetry/run-xy/")
    assert isinstance(carrier, EcsProbeCarrier)
    cfg = carrier.config
    assert cfg.ram_role == "clousight-bench-eci-probe"
    assert cfg.vswitch_id == "vsw-1" and cfg.security_group_id == "sg-1"
    assert cfg.region == "cn-hangzhou" and cfg.run_id == "run-xy"
    # OSS-mediated carrier: campaign_id + bucket drive the ECS env; image_id is a stock OS image.
    assert cfg.campaign_id == "run-xy" and cfg.bucket == "bench-bkt"
    assert cfg.image_id == "aliyun_3_x64_20G_alibase_image"
    assert cfg.instance_type == "ecs.e-c1m2.large"


def test_default_oss_reads_bucket_and_region_from_target():
    from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client

    probe = al._AliyunCampaignProbe()
    oss = probe._default_oss({"oss_bucket": "b", "region": "cn-shanghai"})
    assert isinstance(oss, Oss2Client)
    assert oss._bucket_name == "b" and oss._region == "cn-shanghai"
