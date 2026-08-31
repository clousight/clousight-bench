"""Tests for CampaignChannel — campaign-level OSS objects (ecs prod profile)."""

from clousight_bench.core.blobstore import InMemoryBlobStore
from clousight_bench.core.campaign.channel import CampaignChannel
from clousight_bench.core.campaign.spec import CampaignManifest, LaunchSpec, TaskEntry


def _chan(now=None):
    return CampaignChannel(InMemoryBlobStore(), "camp-1", now=now or (lambda: 0.0))


def test_launch_round_trip():
    ch = _chan()
    assert ch.read_launch() is None
    spec = LaunchSpec(
        campaign_id="camp-1",
        tasks=[{"task_id": "T1.9", "params": {}}],
        params={},
        target={"provider": "aliyun"},
    )
    ch.write_launch(spec)
    assert ch.read_launch() == spec


def test_manifest_round_trip():
    ch = _chan()
    m = CampaignManifest(campaign_id="camp-1", tasks=[TaskEntry(task_id="T1.9")])
    ch.write_manifest(m)
    assert ch.read_manifest() == m


def test_heartbeat_stamps_injected_now():
    ch = _chan(now=lambda: 123.0)
    assert ch.read_heartbeat() is None
    ch.write_heartbeat("T1.9", "provision")
    hb = ch.read_heartbeat()
    assert hb == {"ts": 123.0, "current_task": "T1.9", "phase": "provision"}


def test_append_and_read_logs_in_order():
    ch = _chan()
    ch.append_log("line one")
    ch.append_log("line two")
    assert ch.read_logs() == ["line one", "line two"]


def test_result_json_and_parquet_round_trip():
    ch = _chan()
    ch.write_result("T1.13", b'{"ok": true}', b"PARQUETBYTES")
    j, p = ch.read_result("T1.13")
    assert j == b'{"ok": true}' and p == b"PARQUETBYTES"
    assert ch.list_results() == ["T1.13"]


def test_result_without_parquet():
    ch = _chan()
    ch.write_result("T2.1", b'{"ok": true}', None)
    j, p = ch.read_result("T2.1")
    assert j == b'{"ok": true}' and p is None


def test_ledger_round_trip():
    ch = _chan()
    assert ch.read_ledger() is None
    ch.write_ledger(b"ledger-bytes")
    assert ch.read_ledger() == b"ledger-bytes"


def test_done_marker():
    ch = _chan()
    assert ch.is_done() is None
    ch.write_done(True)
    assert ch.is_done() == "DONE"


def test_failed_marker():
    ch = _chan()
    ch.write_done(False)
    assert ch.is_done() == "FAILED"


def test_stop_signal():
    ch = _chan()
    assert ch.stop_requested() is False
    ch.signal_stop()
    assert ch.stop_requested() is True


def test_claim_is_idempotent():
    ch = _chan()
    assert ch.claim() is True
    assert ch.claim() is False
