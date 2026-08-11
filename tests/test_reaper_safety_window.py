"""A resource with unknown creation time (created_ts=0.0) must never be
age-reaped — it may be an in-flight run. It is only reaped by an untimed sweep.
"""

from clousight_bench.core.resource_tags import TAG_MANAGED, TAG_RUN_ID
from clousight_bench.domains.agent_runtime.reaper import AliyunResourceReaper


def _managed(rid, created_ts):
    return {
        "kind": "agentrun",
        "id": rid,
        "created_ts": created_ts,
        "tags": {TAG_MANAGED: "true", TAG_RUN_ID: "run-x"},
    }


def _reaper(resources, deleted):
    return AliyunResourceReaper(
        list_fns=[lambda: resources],
        delete_fn=lambda kind, rid: deleted.append(rid),
        now=lambda: 1_000_000.0,
    )


def test_unknown_age_resource_is_not_age_reaped():
    deleted = []
    r = _reaper([_managed("in-flight", 0.0)], deleted)
    acted = r.sweep(dry_run=False, older_than_s=3600)
    assert deleted == [], "must not delete a resource whose age is unknown"
    assert acted == []


def test_known_old_resource_is_age_reaped():
    deleted = []
    r = _reaper([_managed("old", 1.0)], deleted)  # created at t=1, now=1e6 -> very old
    r.sweep(dry_run=False, older_than_s=3600)
    assert deleted == ["old"]


def test_unknown_age_resource_is_reaped_by_untimed_sweep():
    deleted = []
    r = _reaper([_managed("orphan", 0.0)], deleted)
    r.sweep(dry_run=False, older_than_s=None)  # untimed sweep still reaps by tag
    assert deleted == ["orphan"]
