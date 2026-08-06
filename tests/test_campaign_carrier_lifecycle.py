import types

from clousight_bench.core.campaign import CampaignManifest, TaskProgress


class _RecordingHook:
    def __init__(self):
        self.events = []
    def start_campaign_probe(self, target):
        self.events.append(("start", dict(target)))
        return {"probe_url": "http://1.2.3.4:9000", "probe_oss_prefix": "campaign-x/"}
    def sync_probe_artifacts(self, results_dir):
        self.events.append(("sync", str(results_dir)))
    def stop_campaign_probe(self):
        self.events.append(("stop", None))


def test_hook_lifecycle_start_sync_stop(monkeypatch, tmp_path):
    from clousight_bench import cli

    hook = _RecordingHook()
    monkeypatch.setattr(cli, "_load_config", lambda p: {"target": {}}, raising=False)
    # Force the hook lookup to return our recorder regardless of provider.
    monkeypatch.setattr("clousight_bench.core.plugin.campaign_probe_hook",
                        lambda provider: hook)
    # Make execute_plan a no-op returning a minimal aggregate so we exercise only
    # the loop's carrier wiring (not real task execution).
    agg = types.SimpleNamespace(plan_id="plan-1", status_counts={"passed": 1})
    monkeypatch.setattr("clousight_bench.core.runplan.execute_plan",
                        lambda *a, **k: agg)

    plan = tmp_path / "plan.yaml"
    plan.write_text(
        "domain: agent_runtime\nplatform: aliyun\n"
        "tasks:\n  - task: T1.4\n  - task: T1.6\n", encoding="utf-8")
    args = types.SimpleNamespace(
        plan_file=str(plan), results=str(tmp_path / "res"), config=None,
        allow_live=False, cost_budget=None, probe="eci")
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
    monkeypatch.setattr("clousight_bench.core.plugin.campaign_probe_hook",
                        lambda provider: hook)
    def _boom(*a, **k):
        raise RuntimeError("task blew up")
    monkeypatch.setattr("clousight_bench.core.runplan.execute_plan", _boom)

    plan = tmp_path / "plan.yaml"
    plan.write_text("domain: agent_runtime\nplatform: aliyun\n"
                    "tasks:\n  - task: T1.4\n", encoding="utf-8")
    args = types.SimpleNamespace(
        plan_file=str(plan), results=str(tmp_path / "res"), config=None,
        allow_live=False, cost_budget=None, probe="eci")
    cli._cmd_run_plan(args)   # task fails, but campaign completes the finally
    assert ("stop", None) in hook.events        # carrier reaped despite task failure
