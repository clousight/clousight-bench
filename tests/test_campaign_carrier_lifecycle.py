"""Tests for the _AliyunCampaignProbe campaign hook lifecycle.

Existing tests (test_hook_lifecycle_start_sync_stop, test_stop_runs_even_on_exception,
test_stop_runs_on_start_campaign_probe_failure) verify the CLI plumbing.

New tests (test_start_returns_oss_shape, test_stop_signals_then_tears_down,
test_stop_swallows_channel_exception, test_stop_before_start_noop,
test_start_injects_ready_check_and_calls_provision) verify _AliyunCampaignProbe
directly with injectable fakes — no real ECI or OSS calls.
"""

import types

# ---------------------------------------------------------------------------
# Shared CLI-level recording hook (used by the three existing CLI tests)
# ---------------------------------------------------------------------------


class _RecordingHook:
    def __init__(self):
        self.events = []

    def start_campaign_probe(self, target):
        self.events.append(("start", dict(target)))
        # Return the new OSS-mediated shape (no probe_url)
        return {
            "probe_control_prefix": "run-abc",
            "probe_oss_prefix": "clousight-bench/telemetry/run-abc/",
            "probe_in_vpc": True,
        }

    def sync_probe_artifacts(self, results_dir):
        self.events.append(("sync", str(results_dir)))

    def stop_campaign_probe(self):
        self.events.append(("stop", None))


# ---------------------------------------------------------------------------
# CLI-level tests (unchanged in purpose)
# ---------------------------------------------------------------------------


def test_hook_lifecycle_start_sync_stop(monkeypatch, tmp_path):
    from clousight_bench import cli

    hook = _RecordingHook()
    monkeypatch.setattr(cli, "_load_config", lambda p: {"target": {}}, raising=False)
    # Force the hook lookup to return our recorder regardless of provider.
    monkeypatch.setattr("clousight_bench.core.plugin.campaign_probe_hook", lambda provider: hook)
    # Make execute_plan a no-op returning a minimal aggregate so we exercise only
    # the loop's carrier wiring (not real task execution).
    agg = types.SimpleNamespace(plan_id="plan-1", status_counts={"passed": 1})
    monkeypatch.setattr("clousight_bench.core.runplan.execute_plan", lambda *a, **k: agg)

    plan = tmp_path / "plan.yaml"
    plan.write_text(
        "domain: agent_runtime\nplatform: aliyun\ntasks:\n  - task: T1.4\n  - task: T1.6\n", encoding="utf-8"
    )
    args = types.SimpleNamespace(
        plan_file=str(plan),
        results=str(tmp_path / "res"),
        config=None,
        allow_live=False,
        cost_budget=None,
        probe="eci",
    )
    rc = cli._cmd_run_plan(args)
    assert rc == 0
    kinds = [e[0] for e in hook.events]
    # start once, sync after each of 2 tasks + a final sync, stop once (last)
    assert kinds[0] == "start"
    assert kinds.count("sync") >= 2
    assert kinds[-1] == "stop"


def test_stop_runs_even_on_exception(monkeypatch, tmp_path):
    from clousight_bench import cli

    hook = _RecordingHook()
    monkeypatch.setattr("clousight_bench.core.plugin.campaign_probe_hook", lambda provider: hook)

    def _boom(*a, **k):
        raise RuntimeError("task blew up")

    monkeypatch.setattr("clousight_bench.core.runplan.execute_plan", _boom)

    plan = tmp_path / "plan.yaml"
    plan.write_text("domain: agent_runtime\nplatform: aliyun\ntasks:\n  - task: T1.4\n", encoding="utf-8")
    args = types.SimpleNamespace(
        plan_file=str(plan),
        results=str(tmp_path / "res"),
        config=None,
        allow_live=False,
        cost_budget=None,
        probe="eci",
    )
    cli._cmd_run_plan(args)  # task fails, but campaign completes the finally
    assert ("stop", None) in hook.events  # carrier reaped despite task failure


def test_stop_runs_on_start_campaign_probe_failure(monkeypatch, tmp_path):
    """start_campaign_probe raising must still trigger stop_campaign_probe.

    Regression guard for the resource-leak bug where start was called OUTSIDE
    the try/finally — a provision failure would skip teardown entirely.
    """
    from clousight_bench import cli

    stop_called = []

    class _FailStartHook:
        def start_campaign_probe(self, target):
            raise RuntimeError("provision timed out")

        def sync_probe_artifacts(self, results_dir):
            pass

        def stop_campaign_probe(self):
            stop_called.append(True)

    hook = _FailStartHook()
    monkeypatch.setattr("clousight_bench.core.plugin.campaign_probe_hook", lambda provider: hook)

    plan = tmp_path / "plan.yaml"
    plan.write_text("domain: agent_runtime\nplatform: aliyun\ntasks:\n  - task: T1.4\n", encoding="utf-8")
    args = types.SimpleNamespace(
        plan_file=str(plan),
        results=str(tmp_path / "res"),
        config=None,
        allow_live=False,
        cost_budget=None,
        probe="eci",
    )

    # The campaign raises because start failed; that's expected.
    try:
        cli._cmd_run_plan(args)
    except Exception:
        pass

    # The critical assertion: stop must have been called even though start raised.
    assert stop_called, "stop_campaign_probe was NOT called after start_campaign_probe raised — carrier leak!"


# ---------------------------------------------------------------------------
# _AliyunCampaignProbe unit tests (offline, injectable fakes)
# ---------------------------------------------------------------------------


def _make_probe(call_log=None):
    """Build a _AliyunCampaignProbe with fake carrier + oss factories.

    call_log: shared list that records events so tests can assert ordering.
    """
    from clousight_bench.domains.agent_runtime.aliyun import _AliyunCampaignProbe
    from clousight_bench.domains.agent_runtime.probe.oss_client import InMemoryOssClient

    if call_log is None:
        call_log = []

    # Fake OSS shared between channel (control side) and tests.
    oss = InMemoryOssClient()

    class _FakeCarrier:
        """Minimal probe carrier stand-in."""

        def __init__(self):
            self.ready_check = None
            self.token = "tok-fake"
            self.call_log = call_log

        def provision(self):
            self.call_log.append("provision")
            return "run-xyz"

        def teardown(self):
            self.call_log.append("teardown")

    fake_carrier = _FakeCarrier()

    def carrier_factory(target, prefix, campaign_id="", bucket=""):
        return fake_carrier

    def oss_factory(target):
        return oss

    probe = _AliyunCampaignProbe(carrier_factory=carrier_factory, oss_factory=oss_factory)
    return probe, fake_carrier, oss, call_log


def test_start_returns_oss_shape():
    """start_campaign_probe returns probe_control_prefix and probe_in_vpc=True, no probe_url."""
    probe, _, _, _ = _make_probe()
    target = {"run_id": "run-xyz", "oss_bucket": "my-bucket"}
    result = probe.start_campaign_probe(target)

    assert "probe_url" not in result, "probe_url must NOT appear in the OSS-mediated return value"
    assert result["probe_control_prefix"] == "run-xyz"
    assert result["probe_oss_prefix"].startswith("clousight-bench/telemetry/")
    assert result["probe_in_vpc"] is True
    assert "probe_token" in result


def test_start_injects_ready_check_and_calls_provision():
    """start_campaign_probe injects channel.is_ready into carrier.ready_check and calls provision."""
    probe, fake_carrier, oss, call_log = _make_probe()
    target = {"run_id": "run-abc", "oss_bucket": "bucket"}
    probe.start_campaign_probe(target)

    # provision must have been called
    assert "provision" in call_log

    # ready_check must be set on the carrier and be a callable
    assert callable(fake_carrier.ready_check), "ready_check must be injected as a callable"

    # Verify that ready_check is indeed channel.is_ready: before writing ready.json it returns False,
    # after writing it returns True.
    from clousight_bench.domains.agent_runtime.probe.oss_channel import OssChannel

    channel = OssChannel(oss, "run-abc")
    assert fake_carrier.ready_check() is False
    channel.write_ready()
    assert fake_carrier.ready_check() is True


def test_stop_signals_then_tears_down():
    """stop_campaign_probe writes stop sentinel to OSS BEFORE tearing down the carrier."""
    probe, fake_carrier, oss, call_log = _make_probe(call_log=[])
    target = {"run_id": "run-abc", "oss_bucket": "bucket"}
    probe.start_campaign_probe(target)

    # Patch signal_stop to record in same call_log
    from clousight_bench.domains.agent_runtime.probe.oss_channel import OssChannel

    original_signal_stop = OssChannel.signal_stop

    def recording_signal_stop(self_channel):
        call_log.append("signal_stop")
        original_signal_stop(self_channel)

    # Directly monkey-patch the channel stored on the probe
    _ch = probe._channel

    def _recording_stop():
        call_log.append("signal_stop")
        original_signal_stop(_ch)

    probe._channel.signal_stop = _recording_stop

    probe.stop_campaign_probe()

    assert call_log == ["provision", "signal_stop", "teardown"], (
        f"Expected [provision, signal_stop, teardown], got {call_log}"
    )

    # Also verify the stop sentinel was actually written to OSS
    channel = OssChannel(oss, "run-abc")
    assert channel.stop_requested() is True


def test_stop_swallows_channel_exception():
    """stop_campaign_probe is interrupt-safe: channel signal_stop exception doesn't prevent teardown."""
    probe, fake_carrier, oss, call_log = _make_probe(call_log=[])
    target = {"run_id": "run-err", "oss_bucket": "bucket"}
    probe.start_campaign_probe(target)

    # Make signal_stop raise
    probe._channel.signal_stop = lambda: (_ for _ in ()).throw(RuntimeError("OSS down"))

    # stop_campaign_probe must not raise and carrier teardown must still run
    probe.stop_campaign_probe()
    assert "teardown" in call_log, "teardown must run even when signal_stop raises"


def test_stop_before_start_noop():
    """Calling stop before start is a harmless no-op (no AttributeError)."""
    from clousight_bench.domains.agent_runtime.aliyun import _AliyunCampaignProbe

    probe = _AliyunCampaignProbe()
    probe.stop_campaign_probe()  # must not raise
