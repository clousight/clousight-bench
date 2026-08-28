import clousight_bench.domains.agent_runtime.aliyun.provider as al
from clousight_bench.domains.agent_runtime.aliyun.ecs_carrier import EcsProbeCarrier


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
        "blob_bucket": "bench-bkt",
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


def test_default_store_reads_bucket_and_region_from_target():
    from clousight_bench.domains.agent_runtime.probe.oss_client import Oss2Client

    probe = al._AliyunCampaignProbe()
    oss = probe._default_store({"blob_bucket": "b", "region": "cn-shanghai"})
    assert isinstance(oss, Oss2Client)
    assert oss._bucket_name == "b" and oss._region == "cn-shanghai"


# --- code_spec resolution (published package vs dev-wheel fallback) -----------


def test_resolve_code_spec_default_pins_to_own_version():
    cs, extra = al._AliyunCampaignProbe._resolve_code_spec({}, "b", "cn-hangzhou", "cid")
    # Pinned to the control plane's own version to avoid probe↔control skew.
    assert cs.startswith("clousight-bench[probe]==") and extra == []


def test_resolve_code_spec_honors_probe_code_spec_override():
    cs, extra = al._AliyunCampaignProbe._resolve_code_spec(
        {"probe_code_spec": "clousight-bench[probe]==0.2.0"}, "b", "cn-hangzhou", "cid"
    )
    assert cs == "clousight-bench[probe]==0.2.0" and extra == []


def _patch_dev_wheel(monkeypatch):
    """Stub the dev-wheel build+upload so no wheel is built and no bucket touched."""
    from clousight_bench.domains.agent_runtime import dev_wheel
    from clousight_bench.domains.agent_runtime.probe import oss_client

    seen = {}

    def _fake_upload(up, sign, cid):
        seen["cid"] = cid
        return f"https://oss-internal/{cid}.whl?sig=x"

    monkeypatch.setattr(oss_client, "Oss2Client", lambda **k: ("oss2", k))
    monkeypatch.setattr(dev_wheel, "upload_dev_wheel", _fake_upload)
    monkeypatch.setattr(dev_wheel, "probe_extra_deps", lambda: ["requests>=2.28", "oss2>=2.18"])
    return seen


def test_resolve_code_spec_dev_wheel_builds_and_presigns(monkeypatch):
    seen = _patch_dev_wheel(monkeypatch)
    cs, extra = al._AliyunCampaignProbe._resolve_code_spec(
        {"probe_dev_wheel": True}, "bkt", "cn-hangzhou", "cid-1"
    )
    assert cs == "https://oss-internal/cid-1.whl?sig=x"
    assert extra == ["requests>=2.28", "oss2>=2.18"]
    assert seen["cid"] == "cid-1"


def test_resolve_code_spec_dev_wheel_accepts_string_flag(monkeypatch):
    _patch_dev_wheel(monkeypatch)
    cs, extra = al._AliyunCampaignProbe._resolve_code_spec(
        {"probe_dev_wheel": "true"}, "bkt", "cn-hangzhou", "cid-2"
    )
    assert cs.startswith("https://oss-internal/") and extra


def test_default_carrier_dev_wheel_populates_config(monkeypatch):
    monkeypatch.setattr(al, "Ecs20140526Sdk", _FakeSdk)
    _patch_dev_wheel(monkeypatch)
    probe = al._AliyunCampaignProbe()
    target = {
        "run_id": "run-xy",
        "blob_bucket": "bench-bkt",
        "region": "cn-hangzhou",
        "eci_probe_role": "clousight-bench-eci-probe",
        "eci_vswitch_id": "vsw-1",
        "eci_security_group_id": "sg-1",
        "ecs_image_id": "aliyun_3_x64_20G_alibase_image",
        "probe_dev_wheel": True,
    }
    carrier = probe._default_carrier(target, "clousight-bench/telemetry/run-xy/", "run-xy", "bench-bkt")
    assert carrier.config.code_spec == "https://oss-internal/run-xy.whl?sig=x"
    assert carrier.config.extra_deps == ["requests>=2.28", "oss2>=2.18"]
