from clousight_bench.domains.agent_runtime.aliyun import AliyunRuntimeProvider
from clousight_bench.domains.agent_runtime.probe.oss_client import InMemoryOssClient


class _FakeCarrier:
    def __init__(self):
        self.up = self.down = False
        self.ready_check = None  # set by start_campaign_probe (OSS heartbeat)

    def provision(self):
        self.up = True
        return "run-x"  # OSS-mediated: provision returns the campaign_id/control prefix

    def teardown(self):
        self.down = True


def test_hook_provisions_stamps_target_and_syncs(tmp_path):
    oss = InMemoryOssClient()
    oss.put_object("clousight-bench/telemetry/run-x/raw-0000.jsonl", b'{"i":0}\n')
    carrier = _FakeCarrier()
    hook = AliyunRuntimeProvider().campaign_probe_hook(
        # OSS-mediated carrier factory takes (target, prefix, campaign_id, bucket).
        carrier_factory=lambda target, prefix, campaign_id="", bucket="": carrier,
        oss_factory=lambda target: oss,
    )
    stamped = hook.start_campaign_probe({"run_id": "run-x", "oss_bucket": "b"})
    assert carrier.up
    # No probe_url anymore — the transport is OSS-mediated (no HTTP surface).
    assert "probe_url" not in stamped
    assert stamped["probe_control_prefix"] == "run-x"
    assert stamped["probe_in_vpc"] is True
    assert stamped["probe_oss_prefix"] == "clousight-bench/telemetry/run-x/"

    hook.sync_probe_artifacts(tmp_path)
    assert (tmp_path / "raw-0000.jsonl").read_bytes() == b'{"i":0}\n'

    hook.stop_campaign_probe()
    assert carrier.down
