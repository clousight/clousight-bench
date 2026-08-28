"""Account-free tests for _AwsCampaignProbe and AwsRuntimeProvider.

Mirrors tests/test_aliyun_campaign_default_carrier.py, swapping
Ecs20140526Sdk → Boto3Ec2Sdk, Oss2Client → S3Client, and
EcsProbeCarrier → Ec2ProbeCarrier.
"""

import clousight_bench.domains.agent_runtime.aws.campaign_probe as cp
from clousight_bench.domains.agent_runtime.aws.carrier import Ec2ProbeCarrier


class _FakeSdk:
    """Fake Ec2Sdk — no network, no boto3."""

    def __init__(self, *a, **k):
        pass

    def run_instance(self, req):
        self.req = req
        return "i-aws-1"

    def describe_instance(self, instance_id):
        return {"status": "running"}

    def delete_instance(self, instance_id):
        return None


def test_default_carrier_builds_ec2_probe_carrier(monkeypatch):
    """_default_carrier returns an Ec2ProbeCarrier with the right config."""
    monkeypatch.setattr(
        "clousight_bench.domains.agent_runtime.aws.campaign_probe.Boto3Ec2Sdk",
        _FakeSdk,
        raising=False,
    )
    # Patch carrier module's Boto3Ec2Sdk too (imported inside _default_carrier)
    import clousight_bench.domains.agent_runtime.aws.carrier as carrier_mod

    monkeypatch.setattr(carrier_mod, "Boto3Ec2Sdk", _FakeSdk)

    probe = cp._AwsCampaignProbe()
    target = {
        "run_id": "run-aws-1",
        "blob_bucket": "my-bench-bucket",
        "region": "us-west-2",
        "probe_subnet_id": "subnet-abc",
        "probe_security_group_id": "sg-xyz",
        "probe_instance_profile": "clousight-bench-probe-role",
        "ec2_image_id": "ami-0abcdef1234567890",
        "ec2_instance_type": "t3.small",
    }
    carrier = probe._default_carrier(target, "clousight-bench/telemetry/run-aws-1/")
    assert isinstance(carrier, Ec2ProbeCarrier)
    cfg = carrier.config
    assert cfg.subnet_id == "subnet-abc"
    assert cfg.security_group_id == "sg-xyz"
    assert cfg.iam_instance_profile == "clousight-bench-probe-role"
    assert cfg.region == "us-west-2"
    assert cfg.run_id == "run-aws-1"
    assert cfg.campaign_id == "run-aws-1"
    assert cfg.bucket == "my-bench-bucket"
    assert cfg.image_id == "ami-0abcdef1234567890"
    assert cfg.instance_type == "t3.small"


def test_default_carrier_falls_back_to_defaults(monkeypatch):
    """_default_carrier uses sensible defaults when optional keys are absent."""
    import clousight_bench.domains.agent_runtime.aws.carrier as carrier_mod

    monkeypatch.setattr(carrier_mod, "Boto3Ec2Sdk", _FakeSdk)

    probe = cp._AwsCampaignProbe()
    carrier = probe._default_carrier(
        {"run_id": "r1", "blob_bucket": "bkt"},
        "clousight-bench/telemetry/r1/",
    )
    assert isinstance(carrier, Ec2ProbeCarrier)
    cfg = carrier.config
    assert cfg.region == "us-east-1"  # default region
    assert cfg.instance_type == "t3.small"  # default instance type
    assert cfg.subnet_id == ""
    assert cfg.security_group_id == ""
    assert cfg.iam_instance_profile == ""


def test_default_store_returns_s3_client():
    """_default_store returns an S3Client with the right bucket/region."""
    from clousight_bench.domains.agent_runtime.probe.s3_client import S3Client

    probe = cp._AwsCampaignProbe()
    oss = probe._default_store({"blob_bucket": "my-bkt", "region": "eu-west-1"})
    assert isinstance(oss, S3Client)
    assert oss._bucket == "my-bkt"
    assert oss._region == "eu-west-1"


def test_default_store_uses_default_region():
    """_default_store defaults region to us-east-1 when not in target."""
    from clousight_bench.domains.agent_runtime.probe.s3_client import S3Client

    probe = cp._AwsCampaignProbe()
    oss = probe._default_store({"blob_bucket": "bkt"})
    assert isinstance(oss, S3Client)
    assert oss._region == "us-east-1"


# ---------------------------------------------------------------------------
# code_spec resolution
# ---------------------------------------------------------------------------


def test_resolve_code_spec_default_pins_to_own_version():
    """Default path pins to the installed clousight-bench version."""
    cs, extra = cp._AwsCampaignProbe._resolve_code_spec({}, "bkt", "us-east-1", "cid")
    assert cs.startswith("clousight-bench[probe]==")
    assert extra == []


def test_resolve_code_spec_honors_probe_code_spec_override():
    """Explicit probe_code_spec override is respected."""
    cs, extra = cp._AwsCampaignProbe._resolve_code_spec(
        {"probe_code_spec": "clousight-bench[probe]==0.9.0"},
        "bkt",
        "us-east-1",
        "cid",
    )
    assert cs == "clousight-bench[probe]==0.9.0"
    assert extra == []


def _patch_dev_wheel(monkeypatch):
    """Stub the dev-wheel build+upload so no wheel is built and no bucket touched."""
    from clousight_bench.domains.agent_runtime import dev_wheel
    from clousight_bench.domains.agent_runtime.probe import s3_client

    seen: dict = {}

    def _fake_upload(up, sign, cid):
        seen["cid"] = cid
        return f"https://s3-internal/{cid}.whl?sig=x"

    monkeypatch.setattr(s3_client, "S3Client", lambda **k: ("s3", k))
    monkeypatch.setattr(dev_wheel, "upload_dev_wheel", _fake_upload)
    monkeypatch.setattr(dev_wheel, "probe_extra_deps", lambda: ["requests>=2.28", "boto3>=1.26"])
    return seen


def test_resolve_code_spec_dev_wheel_builds_and_presigns(monkeypatch):
    """probe_dev_wheel=True triggers dev-wheel upload and presigned URL."""
    seen = _patch_dev_wheel(monkeypatch)
    cs, extra = cp._AwsCampaignProbe._resolve_code_spec(
        {"probe_dev_wheel": True}, "bkt", "us-east-1", "cid-aws-1"
    )
    assert cs == "https://s3-internal/cid-aws-1.whl?sig=x"
    assert extra == ["requests>=2.28", "boto3>=1.26"]
    assert seen["cid"] == "cid-aws-1"


def test_resolve_code_spec_dev_wheel_accepts_string_flag(monkeypatch):
    """probe_dev_wheel='true' (YAML string) is treated as truthy."""
    _patch_dev_wheel(monkeypatch)
    cs, extra = cp._AwsCampaignProbe._resolve_code_spec(
        {"probe_dev_wheel": "true"}, "bkt", "us-east-1", "cid-aws-2"
    )
    assert cs.startswith("https://s3-internal/")
    assert extra


def test_default_carrier_dev_wheel_populates_config(monkeypatch):
    """_default_carrier with probe_dev_wheel sets code_spec to presigned URL."""
    import clousight_bench.domains.agent_runtime.aws.carrier as carrier_mod

    monkeypatch.setattr(carrier_mod, "Boto3Ec2Sdk", _FakeSdk)
    _patch_dev_wheel(monkeypatch)

    probe = cp._AwsCampaignProbe()
    target = {
        "run_id": "run-aws-2",
        "blob_bucket": "my-bench-bucket",
        "region": "us-east-1",
        "probe_subnet_id": "subnet-abc",
        "probe_security_group_id": "sg-xyz",
        "probe_instance_profile": "clousight-bench-probe-role",
        "ec2_image_id": "ami-0abcdef1234567890",
        "probe_dev_wheel": True,
    }
    carrier = probe._default_carrier(
        target, "clousight-bench/telemetry/run-aws-2/", "run-aws-2", "my-bench-bucket"
    )
    assert carrier.config.code_spec == "https://s3-internal/run-aws-2.whl?sig=x"
    assert carrier.config.extra_deps == ["requests>=2.28", "boto3>=1.26"]


# ---------------------------------------------------------------------------
# AwsRuntimeProvider
# ---------------------------------------------------------------------------


def test_aws_runtime_provider_campaign_probe_hook_returns_aws_probe():
    """AwsRuntimeProvider.campaign_probe_hook() returns an _AwsCampaignProbe."""
    from clousight_bench.domains.agent_runtime.aws.provider import AwsRuntimeProvider

    provider = AwsRuntimeProvider()
    hook = provider.campaign_probe_hook()
    assert isinstance(hook, cp._AwsCampaignProbe)


def test_aws_runtime_provider_campaign_probe_hook_injects_factories():
    """campaign_probe_hook passes factory kwargs through to _AwsCampaignProbe."""
    from clousight_bench.domains.agent_runtime.aws.provider import AwsRuntimeProvider

    fake_cf = object()
    fake_of = object()
    provider = AwsRuntimeProvider()
    hook = provider.campaign_probe_hook(carrier_factory=fake_cf, store_factory=fake_of)
    assert hook._carrier_factory is fake_cf
    assert hook._store_factory is fake_of


def test_aws_runtime_provider_str():
    """AwsRuntimeProvider.provider == 'aws'."""
    from clousight_bench.domains.agent_runtime.aws.provider import AwsRuntimeProvider

    assert AwsRuntimeProvider.provider == "aws"
