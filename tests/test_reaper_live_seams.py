# tests/test_reaper_live_seams.py
import types

from clousight_bench.domains.agent_runtime.reaper import AliyunResourceReaper


class _FakeEci:
    def describe_container_groups(self, req):
        cg = types.SimpleNamespace(
            container_group_id="eci-1", creation_time="2026-08-06T00:00:00Z",
            tags=[types.SimpleNamespace(key="clousight-bench:managed", value="true"),
                  types.SimpleNamespace(key="clousight-bench:run-id", value="run-a")])
        return types.SimpleNamespace(
            body=types.SimpleNamespace(container_groups=[cg]))
    def __init__(self): self.deleted = []
    def delete_container_group(self, req): self.deleted.append(req.container_group_id)


class _FakeAgentRun:
    def list_agent_runtimes(self, req):
        managed = types.SimpleNamespace(agent_runtime_id="rt-1",
                                        agent_runtime_name="clousight-bench-abc-0")
        foreign = types.SimpleNamespace(agent_runtime_id="rt-2",
                                        agent_runtime_name="someone-elses-app")
        data = types.SimpleNamespace(items=[managed, foreign])
        return types.SimpleNamespace(body=types.SimpleNamespace(data=data))
    def __init__(self): self.deleted = []
    def delete_agent_runtime(self, rid): self.deleted.append(rid)


def _reaper():
    eci, ar = _FakeEci(), _FakeAgentRun()
    r = AliyunResourceReaper(eci_client=eci, agentrun_client=ar)
    return r, eci, ar


def test_list_eci_maps_tags_and_id():
    r, _, _ = _reaper()
    rows = r._list_eci()
    assert rows[0]["kind"] == "eci" and rows[0]["id"] == "eci-1"
    assert rows[0]["tags"]["clousight-bench:managed"] == "true"
    assert rows[0]["tags"]["clousight-bench:run-id"] == "run-a"


def test_list_agentrun_filters_by_name_prefix_and_synthesizes_managed_tag():
    r, _, _ = _reaper()
    rows = r._list_agentrun()
    ids = [x["id"] for x in rows]
    assert ids == ["rt-1"]                    # foreign app excluded
    assert rows[0]["tags"]["clousight-bench:managed"] == "true"


def test_sweep_confirm_deletes_via_both_clients():
    r, eci, ar = _reaper()
    acted = r.sweep(dry_run=False)
    kinds = sorted(a["kind"] for a in acted)
    assert kinds == ["agentrun", "eci"]
    assert eci.deleted == ["eci-1"]
    assert ar.deleted == ["rt-1"]


def test_default_delete_rejects_unknown_kind():
    r, _, _ = _reaper()
    import pytest
    with pytest.raises(ValueError):
        r._default_delete("rds", "x")
