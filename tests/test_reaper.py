"""Tests for AliyunResourceReaper (account-free, injectable seams)."""
from clousight_bench.domains.agent_runtime.reaper import AliyunResourceReaper


def _fixtures():
    eci = lambda: [
        {"kind": "eci", "id": "eci-1", "created_ts": 100.0,
         "tags": {"clousight-bench:managed": "true", "clousight-bench:run-id": "run-a"}},
        {"kind": "eci", "id": "eci-untagged", "created_ts": 100.0, "tags": {}},
    ]
    runtimes = lambda: [
        {"kind": "agentrun", "id": "rt-1", "created_ts": 50.0,
         "tags": {"clousight-bench:managed": "true", "clousight-bench:run-id": "run-b"}},
    ]
    return [eci, runtimes]


def test_dry_run_lists_managed_only_no_delete():
    deleted = []
    r = AliyunResourceReaper(list_fns=_fixtures(),
                             delete_fn=lambda k, i: deleted.append((k, i)))
    acted = r.sweep(dry_run=True)
    ids = sorted(a["id"] for a in acted)
    assert ids == ["eci-1", "rt-1"]          # untagged skipped
    assert deleted == []                      # dry run deletes nothing
    assert {a["run_id"] for a in acted} == {"run-a", "run-b"}


def test_confirm_deletes_managed_resources():
    deleted = []
    r = AliyunResourceReaper(list_fns=_fixtures(),
                             delete_fn=lambda k, i: deleted.append((k, i)))
    r.sweep(dry_run=False)
    assert sorted(deleted) == [("agentrun", "rt-1"), ("eci", "eci-1")]


def test_older_than_filters_young_resources():
    r = AliyunResourceReaper(list_fns=_fixtures(),
                             delete_fn=lambda k, i: None,
                             now=lambda: 120.0)   # eci age=20s, rt age=70s
    acted = r.sweep(dry_run=True, older_than_s=60.0)
    assert [a["id"] for a in acted] == ["rt-1"]  # only the >60s-old runtime


def test_registered_under_entry_point():
    from clousight_bench.core.registry import get_resource_reaper
    reaper = get_resource_reaper("aliyun")
    assert reaper is not None and reaper.provider == "aliyun"
