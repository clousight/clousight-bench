"""Tests for AliyunResourceReaper (account-free, injectable seams)."""

from clousight_bench.domains.agent_runtime.aliyun.reaper import AliyunResourceReaper


def _fixtures():
    ecs = lambda: [
        {
            "kind": "ecs",
            "id": "ecs-1",
            "created_ts": 100.0,
            "tags": {"clousight-bench:managed": "true", "clousight-bench:run-id": "run-a"},
        },
        {"kind": "ecs", "id": "ecs-untagged", "created_ts": 100.0, "tags": {}},
    ]
    runtimes = lambda: [
        {
            "kind": "agentrun",
            "id": "rt-1",
            "created_ts": 50.0,
            "tags": {"clousight-bench:managed": "true", "clousight-bench:run-id": "run-b"},
        },
    ]
    return [ecs, runtimes]


def test_dry_run_lists_managed_only_no_delete():
    deleted = []
    r = AliyunResourceReaper(list_fns=_fixtures(), delete_fn=lambda k, i: deleted.append((k, i)))
    acted = r.sweep(dry_run=True)
    ids = sorted(a["id"] for a in acted)
    assert ids == ["ecs-1", "rt-1"]  # untagged skipped
    assert deleted == []  # dry run deletes nothing
    assert {a["run_id"] for a in acted} == {"run-a", "run-b"}


def test_confirm_deletes_managed_resources():
    deleted = []
    r = AliyunResourceReaper(list_fns=_fixtures(), delete_fn=lambda k, i: deleted.append((k, i)))
    r.sweep(dry_run=False)
    assert sorted(deleted) == [("agentrun", "rt-1"), ("ecs", "ecs-1")]


def test_older_than_filters_young_resources():
    r = AliyunResourceReaper(
        list_fns=_fixtures(), delete_fn=lambda k, i: None, now=lambda: 120.0
    )  # ecs age=20s, rt age=70s
    acted = r.sweep(dry_run=True, older_than_s=60.0)
    assert [a["id"] for a in acted] == ["rt-1"]  # only the >60s-old runtime


def test_registered_under_entry_point():
    from clousight_bench.core.registry import get_resource_reaper, load_resource_reapers

    reaper = get_resource_reaper("aliyun")
    assert reaper is not None and reaper.provider == "aliyun"
    # Discovery yields both installed reapers (post-move aliyun path + aws).
    reapers = load_resource_reapers()
    assert {"aliyun", "aws"} <= set(reapers)


def test_eci_container_groups_reaped_by_name_prefix():
    # ECI groups named cb-* are managed (synthesized tag); others are skipped.
    eci = lambda: [
        {
            "kind": "eci",
            "id": "eci-1",
            "created_ts": 100.0,
            "tags": {"clousight-bench:managed": "true", "clousight-bench:run-id": "cb-imgp"},
        },
    ]
    deleted = []
    r = AliyunResourceReaper(list_fns=[eci], delete_fn=lambda k, i: deleted.append((k, i)))
    r.sweep(dry_run=False)
    assert deleted == [("eci", "eci-1")]


def test_list_eci_filters_prefix_and_synthesizes_tag():
    import types

    grp_managed = types.SimpleNamespace(
        container_group_name="cb-imgp", container_group_id="eci-1", creation_time="2026-08-13T06:04:21Z"
    )
    grp_foreign = types.SimpleNamespace(
        container_group_name="someone-else", container_group_id="eci-2", creation_time=""
    )
    body = types.SimpleNamespace(container_groups=[grp_managed, grp_foreign])
    fake_eci = types.SimpleNamespace(describe_container_groups=lambda req: types.SimpleNamespace(body=body))
    r = AliyunResourceReaper(eci_client=fake_eci)
    got = r._list_eci()
    assert [g["id"] for g in got] == ["eci-1"]  # foreign group skipped
    assert got[0]["tags"]["clousight-bench:managed"] == "true"
    assert got[0]["created_ts"] > 0.0


def test_carrier_tags_instance_at_creation():
    from clousight_bench.domains.agent_runtime.aliyun.ecs_carrier import EcsCarrierConfig, EcsProbeCarrier

    cfg = EcsCarrierConfig(
        region="cn-hangzhou",
        image_id="img",
        instance_type="ecs.e-c1m2.large",
        vswitch_id="vsw",
        security_group_id="sg",
        ram_role="role",
        bucket="b",
        campaign_id="camp-x",
        run_id="run-20260817-abc",
    )
    carrier = EcsProbeCarrier(sdk=None, config=cfg)
    req = carrier._build_run_request()
    tag_map = {t["key"]: t["value"] for t in req["tag"]}
    assert tag_map["clousight-bench:managed"] == "true"
    assert tag_map["clousight-bench:run-id"] == "run-20260817-abc"
