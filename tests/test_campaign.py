"""A campaign manifest tracks a run-plan's task list through its states and is
written atomically on every transition."""

import json

import pytest

from clousight_bench.core.campaign import (
    CAMPAIGNS_DIRNAME,
    CampaignManifest,
    TaskProgress,
    latest_manifest,
    load_manifest,
    manifest_path,
    new_campaign_id,
    write_manifest,
)


def _manifest(campaign_id="campaign-x", tasks=("T0.1", "T1.1", "T1.3")):
    return CampaignManifest(
        campaign_id=campaign_id,
        plan_file="plan.yaml",
        domain="agent-runtime",
        platform="local-sim",
        tasks=[TaskProgress(task_id=t) for t in tasks],
    )


def test_new_manifest_prefills_every_task_as_pending():
    m = _manifest()
    assert m.total_tasks == 3
    assert [t.status for t in m.tasks] == ["pending", "pending", "pending"]


def test_running_then_done_records_outcome():
    m = _manifest()
    m.mark_running("T1.1")
    assert m._task("T1.1").status == "running"
    assert m._task("T1.1").started_at is not None

    m.mark_done("T1.1", status="completed", plan_id="plan-1", status_counts={"completed": 3})
    t = m._task("T1.1")
    assert t.status == "completed"
    assert t.plan_id == "plan-1"
    assert t.status_counts == {"completed": 3}
    assert t.ended_at is not None
    assert t.elapsed_s is not None and t.elapsed_s >= 0


def test_failed_task_keeps_its_error():
    m = _manifest()
    m.mark_running("T1.3")
    m.mark_done("T1.3", status="failed", error="boom")
    t = m._task("T1.3")
    assert t.status == "failed"
    assert t.error == "boom"


def test_mark_done_rejects_non_terminal_status():
    m = _manifest()
    with pytest.raises(ValueError):
        m.mark_done("T1.1", status="running")


def test_unknown_task_raises():
    m = _manifest()
    with pytest.raises(KeyError):
        m.mark_running("T9.9")


def test_roundtrips_through_disk(tmp_path):
    m = _manifest()
    m.mark_running("T0.1")
    m.mark_done("T0.1", status="completed", plan_id="p", status_counts={"completed": 1})
    path = write_manifest(tmp_path, m)

    assert path == manifest_path(tmp_path, m.campaign_id)
    assert CAMPAIGNS_DIRNAME in path.parts

    reloaded = load_manifest(path)
    assert reloaded.campaign_id == m.campaign_id
    assert reloaded.total_tasks == 3
    assert reloaded._task("T0.1").status == "completed"
    assert reloaded._task("T0.1").status_counts == {"completed": 1}


def test_latest_manifest_picks_most_recent(tmp_path):
    assert latest_manifest(tmp_path) is None
    a = _manifest(campaign_id="campaign-a")
    write_manifest(tmp_path, a)
    b = _manifest(campaign_id="campaign-b")
    b.updated_at = "2099-01-01T00:00:00+0000"
    pb = write_manifest(tmp_path, b)
    # b written last, so its mtime is newest.
    assert latest_manifest(tmp_path) == pb


def test_manifest_is_valid_json_with_kind(tmp_path):
    path = write_manifest(tmp_path, _manifest())
    data = json.loads(path.read_text())
    assert data["kind"] == "campaign_manifest"
    assert data["total_tasks"] == 3


def test_campaign_ids_are_unique():
    assert new_campaign_id() != new_campaign_id()
