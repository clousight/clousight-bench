from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime import AgentRuntimeDomain
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter
from clousight_bench.domains.agent_runtime.tasks.t1_14_idle_timeout_honor import IdleTimeoutHonorTask


def _supported(**over):
    obs = {
        "capability": "supported",
        "configured_idle_s": 10.0,
        "under_wake_ms": 90.0,
        "over_wake_ms": 90090.0,
        "honored": True,
    }
    obs.update(over)
    return ObservationBundle(observations=obs)


def test_score_honored_reports_measurements_no_findings():
    res = IdleTimeoutHonorTask().score(_supported())
    m = res.measurements
    assert m["idle_timeout_capability"].value == "supported"
    assert m["configured_idle_s"].value == 10.0
    assert m["under_wake_ms"].value == 90.0
    assert m["over_wake_ms"].value == 90090.0
    assert m["idle_timeout_honored"].value is True
    assert not res.findings  # honored → clean
    assert "honored=True" in res.notes


def test_score_not_honored_emits_warning():
    res = IdleTimeoutHonorTask().score(_supported(honored=False, over_wake_ms=90.0))
    codes = {f.code: f.severity for f in res.findings}
    assert codes.get("agent_runtime.idle_timeout_not_honored") == "warning"
    assert res.measurements["idle_timeout_honored"].value is False


def test_score_unsupported_marks_task_unsupported():
    res = IdleTimeoutHonorTask().score(
        ObservationBundle(observations={"capability": "unsupported", "reason": "no probe"})
    )
    assert res.unsupported is True
    assert res.measurements["idle_timeout_capability"].value == "unsupported"
    assert any(f.code == "agent_runtime.idle_timeout_probe_absent" for f in res.findings)


def test_local_sim_end_to_end_honored():
    T = AgentRuntimeDomain().tasks()["T1.14"]()
    ad = LocalSimAdapter(
        {
            "mode": "mock",
            "startup": {"cold_ms": 500, "warm_ms": 20},
            "idle_timeout": {"honored": True, "configured_s": 10},
        }
    )
    obs = T.execute(ad, {})
    # honored sim: under stays warm (warm_ms), over pays a cold rebuild (cold+warm)
    assert obs.observations["honored"] is True
    assert obs.observations["under_wake_ms"] == 20.0
    assert obs.observations["over_wake_ms"] == 520.0
    res = T.score(obs)
    assert res.measurements["idle_timeout_honored"].value is True
    assert res.unsupported is False


def test_local_sim_end_to_end_not_honored():
    T = AgentRuntimeDomain().tasks()["T1.14"]()
    ad = LocalSimAdapter(
        {
            "mode": "mock",
            "startup": {"cold_ms": 500, "warm_ms": 20},
            "idle_timeout": {"honored": False, "configured_s": 10},
        }
    )
    res = T.score(T.execute(ad, {}))
    assert res.measurements["idle_timeout_honored"].value is False
    assert any(f.code == "agent_runtime.idle_timeout_not_honored" for f in res.findings)


def test_task_registered_in_domain():
    assert "T1.14" in AgentRuntimeDomain().tasks()
