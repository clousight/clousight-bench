"""Tests for job_progress + chunk_refs fields added in plan-4b (Task 5)."""
from clousight_bench.core.campaign import CampaignManifest, TaskProgress


def _manifest():
    return CampaignManifest(
        campaign_id="campaign-x", plan_file="p.yaml", domain="agent_runtime",
        platform="aliyun", tasks=[TaskProgress(task_id="T1.4")],
    )


def test_mark_progress_sets_live_fields_without_changing_status():
    m = _manifest()
    m.mark_running("T1.4")
    m.mark_progress("T1.4",
                    job_progress={"phase": "burst", "completed": 300, "total": 500},
                    chunk_refs=["campaign-x/job-y/raw-0000.jsonl"])
    t = m.tasks[0]
    assert t.status == "running"                       # unchanged
    assert t.job_progress["completed"] == 300
    assert t.chunk_refs == ["campaign-x/job-y/raw-0000.jsonl"]


def test_new_fields_round_trip_through_dict():
    m = _manifest()
    m.mark_progress("T1.4", job_progress={"phase": "load"}, chunk_refs=["a/b.jsonl"])
    m2 = CampaignManifest.from_dict(m.to_dict())
    assert m2.tasks[0].job_progress == {"phase": "load"}
    assert m2.tasks[0].chunk_refs == ["a/b.jsonl"]


def test_old_manifest_without_new_fields_still_loads():
    # a pre-4b manifest has no job_progress / chunk_refs keys
    legacy = {
        "campaign_id": "c", "plan_file": "p", "domain": "d", "platform": "aliyun",
        "tasks": [{"task_id": "T1.4", "status": "completed"}],
    }
    m = CampaignManifest.from_dict(legacy)
    assert m.tasks[0].job_progress == {} and m.tasks[0].chunk_refs == []


def test_mark_progress_partial_update_leaves_other_field_untouched():
    m = _manifest()
    m.mark_progress("T1.4", chunk_refs=["a", "b"])
    m.mark_progress("T1.4", job_progress={"phase": "running"})   # no chunk_refs arg
    t = m._task("T1.4")
    assert t.chunk_refs == ["a", "b"]          # untouched by the second call
    assert t.job_progress == {"phase": "running"}
    # and the reverse
    m.mark_progress("T1.4", chunk_refs=["c"])                     # no job_progress arg
    assert m._task("T1.4").job_progress == {"phase": "running"}   # untouched
    assert m._task("T1.4").chunk_refs == ["c"]


def test_render_progress_shows_live_job_status():
    from clousight_bench.cli import _render_progress
    m = _manifest()
    m.mark_running("T1.4")
    m.mark_progress("T1.4",
                    job_progress={"phase": "burst", "completed": 300, "total": 500},
                    chunk_refs=["a/raw-0000.jsonl", "a/raw-0001.jsonl"])
    out = _render_progress(m)
    assert "burst 300/500 (60%)" in out
    assert "2 chunk(s) in OSS" in out
