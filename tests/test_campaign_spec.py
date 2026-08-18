"""Tests for campaign launch spec + manifest dataclasses (ecs prod profile)."""

from clousight_bench.core.campaign_spec import CampaignManifest, LaunchSpec, TaskEntry


def test_launch_spec_json_round_trip():
    spec = LaunchSpec(
        campaign_id="camp-1",
        tasks=["T1.9", "T1.13"],
        params={"warmup": 1},
        target={"provider": "aliyun", "region": "cn-hangzhou"},
        watchdog_timeout_s=600.0,
    )
    assert LaunchSpec.from_json(spec.to_json()) == spec


def test_launch_spec_default_watchdog_timeout():
    spec = LaunchSpec(campaign_id="c", tasks=["T0.1"], params={}, target={})
    assert spec.watchdog_timeout_s == 5400.0


def test_manifest_mark_isolates_and_counts():
    m = CampaignManifest(
        campaign_id="camp-1",
        tasks=[TaskEntry(task_id="T1.9"), TaskEntry(task_id="T1.13")],
    )
    m.mark("T1.9", "running", started_ts=100.0)
    # only T1.9 changed
    t199 = next(t for t in m.tasks if t.task_id == "T1.9")
    t1313 = next(t for t in m.tasks if t.task_id == "T1.13")
    assert t199.status == "running" and t199.started_ts == 100.0
    assert t1313.status == "pending" and t1313.started_ts is None
    assert m.counts() == {"pending": 1, "running": 1}


def test_manifest_counts_completed_failed():
    m = CampaignManifest(
        campaign_id="c",
        tasks=[TaskEntry(task_id="A"), TaskEntry(task_id="B"), TaskEntry(task_id="C")],
    )
    m.mark("A", "completed")
    m.mark("B", "failed", error="boom")
    assert m.counts() == {"pending": 1, "completed": 1, "failed": 1}
    assert next(t for t in m.tasks if t.task_id == "B").error == "boom"


def test_manifest_json_round_trip():
    m = CampaignManifest(campaign_id="c", tasks=[TaskEntry(task_id="A", status="completed")])
    assert CampaignManifest.from_json(m.to_json()) == m
