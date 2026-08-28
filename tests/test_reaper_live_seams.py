# tests/test_reaper_live_seams.py
import types

import pytest

from clousight_bench.domains.agent_runtime.aliyun.reaper import AliyunResourceReaper


class _FakeEcs:
    def describe_instances(self, req):
        managed = types.SimpleNamespace(
            instance_id="i-1",
            instance_name="cb-probe-run-a",
            creation_time="2026-08-06T00:00:00Z",
        )
        foreign = types.SimpleNamespace(
            instance_id="i-2",
            instance_name="someone-elses-vm",
            creation_time="2026-08-06T00:00:00Z",
        )
        instances = types.SimpleNamespace(instance=[managed, foreign])
        return types.SimpleNamespace(body=types.SimpleNamespace(instances=instances))

    def __init__(self):
        self.deleted = []

    def delete_instances(self, req):
        self.deleted.extend(req.instance_id)


class _FakeAgentRun:
    def list_agent_runtimes(self, req):
        managed = types.SimpleNamespace(agent_runtime_id="rt-1", agent_runtime_name="clousight-bench-abc-0")
        foreign = types.SimpleNamespace(agent_runtime_id="rt-2", agent_runtime_name="someone-elses-app")
        data = types.SimpleNamespace(items=[managed, foreign])
        return types.SimpleNamespace(body=types.SimpleNamespace(data=data))

    def __init__(self):
        self.deleted = []

    def delete_agent_runtime(self, rid):
        self.deleted.append(rid)


def _reaper():
    ecs, ar = _FakeEcs(), _FakeAgentRun()
    r = AliyunResourceReaper(ecs_client=ecs, agentrun_client=ar)
    return r, ecs, ar


def test_list_ecs_filters_by_name_prefix_and_synthesizes_managed_tag():
    r, _, _ = _reaper()
    rows = r._list_ecs()
    ids = [x["id"] for x in rows]
    assert ids == ["i-1"]  # foreign VM (not cb-probe-*) excluded
    assert rows[0]["kind"] == "ecs"
    assert rows[0]["tags"]["clousight-bench:managed"] == "true"
    assert rows[0]["tags"]["clousight-bench:run-id"] == "cb-probe-run-a"
    assert rows[0]["created_ts"] > 0  # creation_time parsed


def test_list_agentrun_filters_by_name_prefix_and_synthesizes_managed_tag():
    r, _, _ = _reaper()
    rows = r._list_agentrun()
    ids = [x["id"] for x in rows]
    assert ids == ["rt-1"]  # foreign app excluded
    assert rows[0]["tags"]["clousight-bench:managed"] == "true"


@pytest.mark.live  # _reaper() injects no eci_client, so sweep()'s _list_eci hits the real Aliyun ECI endpoint
def test_sweep_confirm_deletes_via_both_clients():
    r, ecs, ar = _reaper()
    acted = r.sweep(dry_run=False)
    kinds = sorted(a["kind"] for a in acted)
    assert kinds == ["agentrun", "ecs"]
    assert ecs.deleted == ["i-1"]
    assert ar.deleted == ["rt-1"]


def test_default_delete_rejects_unknown_kind():
    r, _, _ = _reaper()
    import pytest

    with pytest.raises(ValueError):
        r._default_delete("rds", "x")
