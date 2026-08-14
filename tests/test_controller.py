"""Tests for CampaignController — the serial campaign orchestration loop."""

import pytest

from clousight_bench.core.campaign_spec import LaunchSpec
from clousight_bench.core.controller import CampaignController, TaskOutcome
from clousight_bench.domains.agent_runtime.probe.campaign_channel import CampaignChannel
from clousight_bench.domains.agent_runtime.probe.oss_client import InMemoryOssClient


def _channel():
    return CampaignChannel(InMemoryOssClient(), "camp-1", now=lambda: 1.0)


def test_serial_loop_completes_and_fails_per_task():
    ch = _channel()
    ch.write_launch(LaunchSpec(campaign_id="camp-1", tasks=["T1.9", "T1.13"], params={}, target={}))

    def run_task(task_id, spec):
        if task_id == "T1.13":
            raise RuntimeError("boom")
        return TaskOutcome(task_id=task_id, ok=True, result_json=b'{"t":"%s"}' % task_id.encode())

    CampaignController(ch, run_task, now=lambda: 1.0, ledger_bytes=lambda: b"L").run()

    m = ch.read_manifest()
    by = {t.task_id: t for t in m.tasks}
    assert by["T1.9"].status == "completed"
    assert by["T1.13"].status == "failed" and "boom" in by["T1.13"].error
    assert ch.is_done() == "FAILED"
    assert ch.read_result("T1.9")[0] == b'{"t":"T1.9"}'
    assert ch.read_heartbeat() is not None
    assert ch.read_ledger() == b"L"


def test_all_pass_writes_done():
    ch = _channel()
    ch.write_launch(LaunchSpec(campaign_id="camp-1", tasks=["A"], params={}, target={}))
    CampaignController(
        ch,
        lambda tid, spec: TaskOutcome(task_id=tid, ok=True, result_json=b"{}"),
        now=lambda: 1.0,
        ledger_bytes=lambda: b"",
    ).run()
    assert ch.is_done() == "DONE"
    assert ch.read_manifest().counts() == {"completed": 1}


def test_ok_false_outcome_marks_failed():
    ch = _channel()
    ch.write_launch(LaunchSpec(campaign_id="camp-1", tasks=["A"], params={}, target={}))
    CampaignController(
        ch,
        lambda tid, spec: TaskOutcome(task_id=tid, ok=False, result_json=b"", error="nope"),
        now=lambda: 1.0,
        ledger_bytes=lambda: b"",
    ).run()
    assert ch.read_manifest().tasks[0].status == "failed"
    assert ch.is_done() == "FAILED"


def test_stop_signal_breaks_before_next_task():
    ch = _channel()
    ch.write_launch(LaunchSpec(campaign_id="camp-1", tasks=["A", "B"], params={}, target={}))
    ch.signal_stop()
    CampaignController(
        ch,
        lambda tid, spec: TaskOutcome(task_id=tid, ok=True, result_json=b"{}"),
        now=lambda: 1.0,
        ledger_bytes=lambda: b"",
    ).run()
    # stopped before running anything
    assert ch.read_manifest().counts().get("pending") == 2
