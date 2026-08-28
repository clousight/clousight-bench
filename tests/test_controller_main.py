"""Tests for cb-controller entrypoint wiring (build_run_task + build factory)."""

from types import SimpleNamespace

from clousight_bench.core import controller_main
from clousight_bench.core.blobstore import InMemoryBlobStore
from clousight_bench.core.campaign_channel import CampaignChannel
from clousight_bench.core.campaign_spec import LaunchSpec
from clousight_bench.core.controller import CampaignController, TaskOutcome
from clousight_bench.core.watchdog import SelfDestructWatchdog


def _fake_record(status="completed"):
    return SimpleNamespace(
        status=status,
        errors=[],
        to_json=lambda: f'{{"status": "{status}"}}',
        identity=SimpleNamespace(domain="agent-runtime", adapter="aliyun-agentrun"),
        run=SimpleNamespace(run_id="run-xyz"),
    )


def test_build_run_task_wraps_execute_into_outcome(monkeypatch, tmp_path):
    captured = {}

    def fake_execute(spec, **kw):
        captured["spec"] = spec
        captured["kw"] = kw
        return _fake_record("completed")

    monkeypatch.setattr(controller_main, "execute", fake_execute)
    rt = controller_main.build_run_task("aliyun-agentrun", tmp_path)
    spec = LaunchSpec(
        campaign_id="c",
        tasks=[{"task_id": "T1.9", "params": {"repeat": 2}}],
        params={"warmup": 1},
        target={"provider": "aliyun"},
        cost_budget=12.5,
    )
    outcome = rt("T1.9", spec)

    assert isinstance(outcome, TaskOutcome)
    assert outcome.ok is True
    assert outcome.result_json == b'{"status": "completed"}'
    assert outcome.series_parquet is None  # no sidecar on disk
    # execute got a RunSpec carrying the target + merged (global ∪ per-task) params
    assert captured["spec"].task_id == "T1.9"
    assert captured["spec"].target == {"provider": "aliyun"}
    assert captured["spec"].params == {"warmup": 1, "repeat": 2}
    # allow_live + the campaign cost budget are forwarded into execute
    assert captured["kw"]["allow_live"] is True
    assert captured["kw"]["cost_budget"] == 12.5


def test_build_run_task_runs_suite_task_with_params(monkeypatch, tmp_path):
    captured = {}

    def fake_execute(spec, **kw):
        captured["spec"] = spec
        captured["kw"] = kw
        return _fake_record("completed")

    monkeypatch.setattr(controller_main, "execute", fake_execute)
    rt = controller_main.build_run_task("aliyun-agentrun", tmp_path)
    spec = LaunchSpec(
        campaign_id="c",
        tasks=[{"task_id": "suite:swe-bench", "params": {"subset": "verified-50"}}],
        params={},
        target={"provider": "aliyun"},
    )
    rt("suite:swe-bench", spec)
    assert captured["spec"].task_id == "suite:swe-bench"
    assert captured["spec"].params == {"subset": "verified-50"}
    assert captured["kw"]["cost_budget"] is None  # no budget in the launch spec


def test_build_run_task_marks_failed_status(monkeypatch, tmp_path):
    monkeypatch.setattr(controller_main, "execute", lambda spec, **kw: _fake_record("failed"))
    rt = controller_main.build_run_task("aliyun-agentrun", tmp_path)
    spec = LaunchSpec(campaign_id="c", tasks=[{"task_id": "T1.9", "params": {}}], params={}, target={})
    outcome = rt("T1.9", spec)
    assert outcome.ok is False and outcome.error == "failed"


def test_build_reaper_wires_deleters_in_order():
    calls: list[str] = []
    reaper = controller_main.build_reaper(
        {"CB_REGION": "cn-hangzhou"},
        results_dir="/unused",
        instance_id="i-self",
        live_runtimes=lambda: ["rt-a", "rt-b"],
        delete_runtime=lambda rid: calls.append(f"rt:{rid}"),
        delete_nat=lambda: calls.append("nat"),
        delete_self=lambda iid: calls.append(f"self:{iid}"),
    )
    errors = reaper.reap()
    assert errors == []
    # runtimes first (both), then NAT, then self LAST
    assert calls == ["rt:rt-a", "rt:rt-b", "nat", "self:i-self"]


def test_build_reaper_defaults_live_runtimes_to_ledger(tmp_path):
    from clousight_bench.core.resource_ledger import ResourceLedger

    led = ResourceLedger(tmp_path)
    led.record_created("run-1", "aliyun", "rt-live", "runtime")
    led.record_created("run-1", "aliyun", "rt-gone", "runtime")
    led.mark_deleted("run-1", "rt-gone")

    seen: list[str] = []
    reaper = controller_main.build_reaper(
        {"CB_REGION": "cn-hangzhou"},
        results_dir=tmp_path,
        instance_id="i-self",
        delete_runtime=lambda rid: seen.append(rid),
        delete_nat=lambda: None,
        delete_self=lambda iid: None,
    )
    reaper.reap()
    assert seen == ["rt-live"]  # created-not-deleted only


def test_build_reaper_degrades_to_noop_when_no_provider(monkeypatch, tmp_path):
    """With no runtime provider resolvable (open-core / unknown platform), the
    unfilled deleters become logging no-ops — reap() runs clean, never raising,
    and never touching a cloud SDK. Teardown falls to the local backstop."""
    import clousight_bench.core.registry as registry

    monkeypatch.setattr(registry, "get_runtime_provider", lambda p: None)
    logged: list[str] = []
    reaper = controller_main.build_reaper(
        {"CB_REGION": "cn-hangzhou"},  # no CB_PLATFORM → provider unresolvable
        results_dir=tmp_path,
        instance_id="i-self",
        live_runtimes=lambda: ["rt-a"],
        log=logged.append,
    )
    assert reaper.reap() == []  # no-ops collect no errors
    assert any("no runtime provider reaper wired" in m for m in logged)


def test_build_reaper_uses_provider_spec_defaults(monkeypatch, tmp_path):
    """When deleters are NOT injected, build_reaper pulls them from the resolved
    provider's ControllerReaperSpec (region + log threaded through)."""
    from clousight_bench.core.plugin import ControllerReaperSpec

    calls: list[str] = []
    captured: dict = {}

    class _Prov:
        def controller_reaper_spec(self, region, log):  # noqa: ANN001, ANN202
            captured["region"] = region
            return ControllerReaperSpec(
                delete_runtime=lambda rid: calls.append(f"rt:{rid}"),
                delete_nat=lambda: calls.append("nat"),
                delete_self=lambda iid: calls.append(f"self:{iid}"),
                self_instance_id=lambda: "i-from-spec",
            )

    import clousight_bench.core.registry as registry

    monkeypatch.setattr(registry, "get_runtime_provider", lambda p: _Prov())
    reaper = controller_main.build_reaper(
        {"CB_REGION": "cn-shanghai", "CB_PLATFORM": "aliyun-agentrun"},
        results_dir=tmp_path,
        live_runtimes=lambda: ["rt-a"],
    )
    assert captured["region"] == "cn-shanghai"
    assert reaper.reap() == []
    assert calls == ["rt:rt-a", "nat", "self:i-from-spec"]  # id from the spec


def test_build_wires_controller_and_watchdog():
    oss = InMemoryBlobStore()
    CampaignChannel(oss, "camp-1").write_launch(
        LaunchSpec(
            campaign_id="camp-1",
            tasks=[{"task_id": "T1.9", "params": {}}],
            params={},
            target={},
            watchdog_timeout_s=99.0,
        )
    )
    reaper = SimpleNamespace(reap=lambda: None)
    controller, watchdog = controller_main.build(
        {"CB_CAMPAIGN_ID": "camp-1"},
        oss,
        run_task=lambda tid, spec: TaskOutcome(task_id=tid, ok=True, result_json=b"{}"),
        reaper=reaper,
    )
    assert isinstance(controller, CampaignController)
    assert isinstance(watchdog, SelfDestructWatchdog)
    assert watchdog._timeout_s == 99.0  # read from the launch spec
