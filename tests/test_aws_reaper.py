"""Tests for AwsResourceReaper (account-free, injectable seams).

Mirrors tests/test_reaper.py (list_fns/delete_fn injection) and
tests/test_reaper_live_seams.py (fake SDK clients).
"""

from __future__ import annotations

import datetime

from clousight_bench.core.resource_tags import TAG_MANAGED, TAG_RUN_ID
from clousight_bench.domains.agent_runtime.aws.reaper import AwsResourceReaper

# ---------------------------------------------------------------------------
# Part 1: account-free injection tests (mirror test_reaper.py)
# ---------------------------------------------------------------------------


def _fixtures():
    ec2 = lambda: [
        {
            "kind": "ec2",
            "id": "i-aaa",
            "created_ts": 100.0,
            "tags": {TAG_MANAGED: "true", TAG_RUN_ID: "cb-probe-run-a"},
        },
        {"kind": "ec2", "id": "i-untagged", "created_ts": 100.0, "tags": {}},
    ]
    runtimes = lambda: [
        {
            "kind": "agentcore",
            "id": "rt-1",
            "created_ts": 50.0,
            "tags": {TAG_MANAGED: "true", TAG_RUN_ID: "clousight-bench-abc-0"},
        },
    ]
    return [ec2, runtimes]


def test_dry_run_lists_managed_only_no_delete():
    deleted = []
    r = AwsResourceReaper(list_fns=_fixtures(), delete_fn=lambda k, i: deleted.append((k, i)))
    acted = r.sweep(dry_run=True)
    ids = sorted(a["id"] for a in acted)
    assert ids == ["i-aaa", "rt-1"]  # untagged skipped
    assert deleted == []  # dry run deletes nothing
    assert {a["run_id"] for a in acted} == {"cb-probe-run-a", "clousight-bench-abc-0"}


def test_confirm_deletes_managed_resources():
    deleted = []
    r = AwsResourceReaper(list_fns=_fixtures(), delete_fn=lambda k, i: deleted.append((k, i)))
    r.sweep(dry_run=False)
    assert sorted(deleted) == [("agentcore", "rt-1"), ("ec2", "i-aaa")]


def test_older_than_filters_young_resources():
    r = AwsResourceReaper(
        list_fns=_fixtures(), delete_fn=lambda k, i: None, now=lambda: 120.0
    )  # ec2 age=20s, rt age=70s
    acted = r.sweep(dry_run=True, older_than_s=60.0)
    assert [a["id"] for a in acted] == ["rt-1"]  # only the >60s-old runtime


def test_age_failsafe_zero_created_ts_skipped_when_older_than_set():
    """Resources with created_ts==0.0 must NOT be reaped when older_than_s is set."""
    agentcore = lambda: [
        {
            "kind": "agentcore",
            "id": "rt-unknown-age",
            "created_ts": 0.0,
            "tags": {TAG_MANAGED: "true", TAG_RUN_ID: "clousight-bench-x"},
        }
    ]
    r = AwsResourceReaper(list_fns=[agentcore], delete_fn=lambda k, i: None, now=lambda: 9999.0)
    acted = r.sweep(dry_run=True, older_than_s=1.0)
    assert acted == []  # fail-safe: unknown age is never reaped by age


# ---------------------------------------------------------------------------
# Part 2: fake-client seam tests (mirror test_reaper_live_seams.py)
# ---------------------------------------------------------------------------


class _FakeEc2:
    def __init__(self):
        self.terminated = []

    def describe_instances(self, **kwargs):
        launch = datetime.datetime(2026, 8, 6, 0, 0, 0, tzinfo=datetime.timezone.utc)
        managed = {
            "InstanceId": "i-probe-1",
            "State": {"Name": "running"},
            "LaunchTime": launch,
            "Tags": [{"Key": "Name", "Value": "cb-probe-run-a"}],
        }
        foreign = {
            "InstanceId": "i-foreign-2",
            "State": {"Name": "running"},
            "LaunchTime": launch,
            "Tags": [{"Key": "Name", "Value": "someone-elses-vm"}],
        }
        return {"Reservations": [{"Instances": [managed, foreign]}]}

    def terminate_instances(self, **kwargs):
        self.terminated.extend(kwargs.get("InstanceIds", []))


class _FakeAgentCore:
    def __init__(self):
        self.deleted = []

    def list_agent_runtimes(self, **kwargs):
        return {
            "agentRuntimes": [
                {"agentRuntimeId": "rt-1", "agentRuntimeName": "clousight-bench-abc-0"},
                {"agentRuntimeId": "rt-2", "agentRuntimeName": "someone-elses-app"},
            ]
        }

    def delete_agent_runtime(self, **kwargs):
        self.deleted.append(kwargs.get("agentRuntimeId"))


def _reaper():
    ec2, ac = _FakeEc2(), _FakeAgentCore()
    r = AwsResourceReaper(ec2_client=ec2, agentcore_client=ac)
    return r, ec2, ac


def test_list_ec2_filters_by_name_prefix_and_synthesizes_managed_tag():
    r, _, _ = _reaper()
    rows = r._list_ec2()
    ids = [x["id"] for x in rows]
    assert ids == ["i-probe-1"]  # foreign VM (not cb-probe-*) excluded
    assert rows[0]["kind"] == "ec2"
    assert rows[0]["tags"][TAG_MANAGED] == "true"
    assert rows[0]["tags"][TAG_RUN_ID] == "cb-probe-run-a"
    assert rows[0]["created_ts"] > 0  # LaunchTime parsed to epoch


def test_list_agentcore_filters_by_name_prefix_and_synthesizes_managed_tag():
    r, _, _ = _reaper()
    rows = r._list_agentcore()
    ids = [x["id"] for x in rows]
    assert ids == ["rt-1"]  # foreign app excluded
    assert rows[0]["tags"][TAG_MANAGED] == "true"
    assert rows[0]["created_ts"] == 0.0  # API doesn't expose creation time


def test_sweep_confirm_deletes_via_both_clients():
    r, ec2, ac = _reaper()
    acted = r.sweep(dry_run=False)
    kinds = sorted(a["kind"] for a in acted)
    assert kinds == ["agentcore", "ec2"]
    assert ec2.terminated == ["i-probe-1"]
    assert ac.deleted == ["rt-1"]


def test_default_delete_rejects_unknown_kind():
    import pytest

    r, _, _ = _reaper()
    with pytest.raises(ValueError):
        r._default_delete("rds", "x")


def test_terminated_state_ec2_instances_excluded():
    """Terminated and shutting-down EC2 instances must be skipped."""
    import datetime

    class _FakeEc2Terminated:
        def describe_instances(self, **kwargs):
            launch = datetime.datetime(2026, 8, 6, tzinfo=datetime.timezone.utc)
            return {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-dead",
                                "State": {"Name": "terminated"},
                                "LaunchTime": launch,
                                "Tags": [{"Key": "Name", "Value": "cb-probe-dead"}],
                            },
                            {
                                "InstanceId": "i-stopping",
                                "State": {"Name": "shutting-down"},
                                "LaunchTime": launch,
                                "Tags": [{"Key": "Name", "Value": "cb-probe-stopping"}],
                            },
                        ]
                    }
                ]
            }

    r = AwsResourceReaper(ec2_client=_FakeEc2Terminated(), agentcore_client=_FakeAgentCore())
    rows = r._list_ec2()
    assert rows == []  # both excluded
