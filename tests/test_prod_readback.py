"""Tests for prod status / logs / fetch readback."""

from clousight_bench.core.blobstore import InMemoryBlobStore
from clousight_bench.core.campaign import prod_submit
from clousight_bench.core.campaign.channel import CampaignChannel
from clousight_bench.core.campaign.spec import CampaignManifest, TaskEntry


def _seed():
    oss = InMemoryBlobStore()
    ch = CampaignChannel(oss, "camp-1", now=lambda: 100.0)
    ch.write_manifest(
        CampaignManifest(
            campaign_id="camp-1",
            tasks=[TaskEntry("A", status="completed"), TaskEntry("B", status="running")],
        )
    )
    ch.write_heartbeat("B", "run")  # ts=100.0
    ch.append_log("log one")
    ch.append_log("log two")
    ch.write_result("A", b'{"a": 1}', b"PARQ")
    return ch


def test_status_fresh_heartbeat_not_stale():
    ch = _seed()
    st = prod_submit.status(ch, now=lambda: 110.0)  # 10s after hb → fresh
    assert st["counts"] == {"completed": 1, "running": 1}
    assert st["current_task"] == "B"
    assert st["heartbeat_age_s"] == 10.0
    assert st["stale"] is False


def test_status_old_heartbeat_is_stale():
    ch = _seed()
    st = prod_submit.status(ch, now=lambda: 200.0)  # 100s > 2*15 → stale
    assert st["stale"] is True


def test_logs_returns_lines_in_order():
    ch = _seed()
    assert prod_submit.logs(ch) == ["log one", "log two"]


def test_fetch_downloads_json_and_parquet(tmp_path):
    ch = _seed()
    written = prod_submit.fetch(ch, tmp_path)
    names = sorted(p.name for p in written)
    assert names == ["A.json", "A.series.parquet"]
    assert (tmp_path / "A.json").read_bytes() == b'{"a": 1}'
    assert (tmp_path / "A.series.parquet").read_bytes() == b"PARQ"
