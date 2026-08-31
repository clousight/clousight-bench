"""Tests for campaign launch spec + manifest dataclasses (ecs prod profile)."""

import pytest

from clousight_bench.core.campaign.spec import CampaignManifest, LaunchSpec, TaskEntry


def test_launch_spec_json_round_trip():
    spec = LaunchSpec(
        campaign_id="camp-1",
        tasks=[
            {"task_id": "suite:swe-bench", "params": {"subset": "verified-50"}},
            {"task_id": "T1.13", "params": {}},
        ],
        params={"warmup": 1},
        target={"provider": "aliyun", "region": "cn-hangzhou"},
        watchdog_timeout_s=600.0,
        cost_budget=25.0,
    )
    assert LaunchSpec.from_json(spec.to_json()) == spec


def test_launch_spec_defaults_watchdog_and_no_budget():
    spec = LaunchSpec(campaign_id="c", tasks=[{"task_id": "T0.1", "params": {}}], params={}, target={})
    assert spec.watchdog_timeout_s == 5400.0
    assert spec.cost_budget is None


def test_launch_spec_from_json_normalizes_missing_params_and_budget():
    raw = b'{"campaign_id": "c", "tasks": [{"task_id": "A"}]}'
    spec = LaunchSpec.from_json(raw)
    assert spec.tasks == [{"task_id": "A", "params": {}}]
    assert spec.cost_budget is None


def test_launch_spec_task_params_lookup():
    spec = LaunchSpec(
        campaign_id="c",
        tasks=[{"task_id": "A", "params": {"k": 1}}, {"task_id": "B", "params": {}}],
        params={},
        target={},
    )
    assert spec.task_params("A") == {"k": 1}
    assert spec.task_params("B") == {}
    with pytest.raises(KeyError):
        spec.task_params("missing")


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
