"""Tests for prod submit + teardown local logic."""

from clousight_bench.core import prod_submit
from clousight_bench.core.resource_ledger import LEDGER_FILE, ResourceLedger
from clousight_bench.domains.agent_runtime.probe.campaign_channel import CampaignChannel
from clousight_bench.domains.agent_runtime.probe.oss_client import InMemoryOssClient


def _write(p, text):
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_submit_writes_launch_and_applies_terraform(tmp_path):
    plan = _write(tmp_path / "plan.yaml", "tasks:\n  - task: T1.9\n  - task: T1.13\n")
    config = _write(
        tmp_path / "cfg.yaml",
        'params: {"warmup": 1}\ntarget: {"provider": "aliyun", "region": "cn-hangzhou"}\n',
    )
    oss = InMemoryOssClient()
    tf_calls = []
    cid = prod_submit.submit(
        plan,
        config,
        channel_factory=lambda c: CampaignChannel(oss, c),
        terraform=lambda argv: tf_calls.append(argv) or 0,
        watchdog_timeout_s=600.0,
        gen_id=lambda: "camp-x",
    )
    assert cid == "camp-x"
    spec = CampaignChannel(oss, "camp-x").read_launch()
    assert spec.tasks == ["T1.9", "T1.13"]
    assert spec.target["provider"] == "aliyun" and spec.params == {"warmup": 1}
    assert spec.watchdog_timeout_s == 600.0
    assert tf_calls[0][0] == "apply"
    assert "enable_controller=true" in tf_calls[0] and "enable_nat=true" in tf_calls[0]


def test_submit_with_wheel_builder_injects_wheel_vars(tmp_path):
    plan = _write(tmp_path / "plan.yaml", "tasks:\n  - task: T1.13\n")
    config = _write(
        tmp_path / "cfg.yaml", 'params: {}\ntarget: {"oss_bucket": "b", "region": "cn-hangzhou"}\n'
    )
    oss = InMemoryOssClient()
    tf_calls = []
    prod_submit.submit(
        plan,
        config,
        channel_factory=lambda c: CampaignChannel(oss, c),
        terraform=lambda argv: tf_calls.append(argv) or 0,
        watchdog_timeout_s=600.0,
        wheel_builder=lambda cid: ("https://p/w.whl", ["requests>=2.28", "duckdb>=1.0"]),
        gen_id=lambda: "camp-w",
    )
    argv = tf_calls[0]
    assert "controller_wheel_url=https://p/w.whl" in argv
    assert 'controller_extra_deps=["requests>=2.28", "duckdb>=1.0"]' in argv


def test_teardown_stops_reaps_residual_and_destroys(tmp_path):
    oss = InMemoryOssClient()
    ch = CampaignChannel(oss, "camp-1")
    # seed an OSS ledger snapshot listing a still-live runtime r9
    led = ResourceLedger(tmp_path)
    led.record_created("run-1", "aliyun", "r9", "runtime")
    ch.write_ledger((tmp_path / LEDGER_FILE).read_bytes())

    deleted = []
    tf_calls = []
    out = prod_submit.teardown(
        ch,
        terraform=lambda argv: tf_calls.append(argv) or 0,
        delete_runtime=lambda rid: deleted.append(rid),
    )
    assert ch.stop_requested() is True
    assert deleted == ["r9"]
    assert out == {"destroyed": True, "residual_deleted": ["r9"]}
    assert tf_calls[0][0] == "destroy" and "enable_nat=false" in tf_calls[0]
