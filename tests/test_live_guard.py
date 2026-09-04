"""P0-1: live-run cost/confirmation gate.

A run whose numbers come from a REAL cloud (execution_mode == "live") spends
real money and can trip quota / abuse controls. It must not provision unless the
operator explicitly acknowledged the cost. Simulated runs are never gated.
"""

from clousight_bench.core.live_guard import ENV_ALLOW_LIVE, live_decision
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec
from clousight_bench.domains.agent_runtime.adapters.cn_clouds import AliyunAgentRunAdapter
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter

# --- unit: the pure decision ------------------------------------------------


def test_simulated_run_is_never_gated():
    d = live_decision("simulated", target={}, allow_live=False, env={})
    assert d.is_live is False
    assert d.blocked is False


def test_live_run_without_ack_is_blocked():
    d = live_decision("live", target={}, allow_live=False, env={})
    assert d.is_live is True
    assert d.blocked is True


def test_live_run_with_flag_is_allowed():
    d = live_decision("live", target={}, allow_live=True, env={})
    assert d.blocked is False
    assert d.acknowledged is True


def test_live_run_with_env_ack_is_allowed():
    d = live_decision("live", target={}, allow_live=False, env={ENV_ALLOW_LIVE: "1"})
    assert d.blocked is False


def test_live_limits_are_carried_from_target():
    d = live_decision(
        "live", target={"live_limits": {"max_concurrency": 4, "max_duration_s": 30}}, allow_live=True, env={}
    )
    assert d.limits == {"max_concurrency": 4, "max_duration_s": 30}


# --- integration: the orchestrator gate -------------------------------------


def test_live_run_blocked_before_setup(tmp_path, monkeypatch):
    # aliyun-agentrun in mode: mock is runnable end-to-end; force it to declare
    # live so the billable-provider gate fires.
    monkeypatch.setattr(AliyunAgentRunAdapter, "execution_mode", lambda self: "live")
    setup_calls: list[int] = []
    monkeypatch.setattr(AliyunAgentRunAdapter, "setup", lambda self: setup_calls.append(1))
    spec = RunSpec("agent-runtime", "suite:stub.ok", "aliyun-agentrun", target={"mode": "mock"})
    rec = execute(spec, results_dir=tmp_path, preflight=False, allow_live=False)
    assert rec.status == "invalid"
    assert any(f["code"] == "live.unconfirmed" for f in rec.findings)
    assert rec.run.stages.get("SETUP") != "ok"
    assert setup_calls == []  # nothing was provisioned


def test_live_run_proceeds_when_acknowledged(tmp_path, monkeypatch):
    monkeypatch.setattr(AliyunAgentRunAdapter, "execution_mode", lambda self: "live")
    spec = RunSpec("agent-runtime", "suite:stub.ok", "aliyun-agentrun", target={"mode": "mock"})
    rec = execute(spec, results_dir=tmp_path, preflight=False, allow_live=True)
    assert rec.status == "completed"
    assert rec.extensions["core"]["live_run"]["acknowledged"] is True


def test_provider_less_simulator_is_never_gated(tmp_path, monkeypatch):
    # A provider-less local adapter cannot bill a cloud even if it (mis)declares
    # execution_mode "live" -- it must never be blocked.
    monkeypatch.setattr(LocalSimAdapter, "execution_mode", lambda self: "live")
    spec = RunSpec("agent-runtime", "suite:stub.ok", "local-sim", target={})
    rec = execute(spec, results_dir=tmp_path, preflight=False, allow_live=False)
    assert rec.status == "completed"
