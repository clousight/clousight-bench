"""Tests for SelfDestructWatchdog — terminal detection + one-shot reap."""

from clousight_bench.core.blobstore import InMemoryBlobStore
from clousight_bench.core.campaign_channel import CampaignChannel
from clousight_bench.core.watchdog import SelfDestructWatchdog


def _channel(now=lambda: 0.0):
    return CampaignChannel(InMemoryBlobStore(), "camp-1", now=now)


def test_should_stop_on_done():
    ch = _channel()
    ch.write_done(True)
    wd = SelfDestructWatchdog(ch, reap=lambda: None, timeout_s=100.0, now=lambda: 0.0)
    assert wd.should_stop(start_ts=0.0) == "done"


def test_should_stop_on_timeout():
    ch = _channel()
    wd = SelfDestructWatchdog(ch, reap=lambda: None, timeout_s=10.0, now=lambda: 50.0)
    assert wd.should_stop(start_ts=0.0) == "timeout"


def test_should_stop_on_stop_signal():
    ch = _channel()
    ch.signal_stop()
    wd = SelfDestructWatchdog(ch, reap=lambda: None, timeout_s=100.0, now=lambda: 0.0)
    assert wd.should_stop(start_ts=0.0) == "stop"


def test_should_stop_none_when_running():
    ch = _channel()
    wd = SelfDestructWatchdog(ch, reap=lambda: None, timeout_s=100.0, now=lambda: 5.0)
    assert wd.should_stop(start_ts=0.0) is None


def test_run_until_terminal_reaps_once_and_returns_reason():
    ch = _channel()
    ch.write_done(True)
    calls = []
    wd = SelfDestructWatchdog(ch, reap=lambda: calls.append("reap"), timeout_s=100.0, now=lambda: 0.0)
    reason = wd.run_until_terminal(start_ts=0.0, poll=lambda: None, sleep=lambda s: None)
    assert reason == "done"
    assert calls == ["reap"]
