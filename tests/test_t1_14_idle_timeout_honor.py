from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime import AgentRuntimeDomain
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter
from clousight_bench.domains.agent_runtime.tasks.t1_14_idle_timeout_honor import IdleTimeoutHonorTask


def _supported(**over):
    obs = {
        "capability": "supported",
        "configured_idle_s": 10.0,
        "promise_wake_ms": 90.0,
        "honored": True,
        "deep_onset_s": 180.0,
        "cold_onset_s": 300.0,
        "decay_capped": False,
    }
    obs.update(over)
    return ObservationBundle(observations=obs)


def test_score_honored_reports_measurements_and_decay():
    res = IdleTimeoutHonorTask().score(_supported())
    m = res.measurements
    assert m["idle_timeout_capability"].value == "supported"
    assert m["configured_idle_s"].value == 10.0
    assert m["promise_wake_ms"].value == 90.0
    assert m["idle_timeout_honored"].value is True
    assert m["deep_onset_s"].value == 180.0
    assert m["cold_onset_s"].value == 300.0
    # honored → no warning; cold_onset seen → info finding about post-promise recycle
    codes = {f.code: f.severity for f in res.findings}
    assert "agent_runtime.idle_timeout_not_honored" not in codes
    assert codes.get("agent_runtime.idle_recycle_after_promise") == "info"
    assert "honored=True" in res.notes


def test_score_promise_broken_emits_warning():
    res = IdleTimeoutHonorTask().score(_supported(honored=False, promise_wake_ms=90000.0))
    codes = {f.code: f.severity for f in res.findings}
    assert codes.get("agent_runtime.idle_timeout_not_honored") == "warning"
    assert res.measurements["idle_timeout_honored"].value is False


def test_score_warm_beyond_sweep_emits_info():
    res = IdleTimeoutHonorTask().score(_supported(cold_onset_s=None, decay_capped=True))
    codes = {f.code for f in res.findings}
    assert "agent_runtime.idle_warm_beyond_sweep" in codes


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
            "idle_timeout": {"honored": True, "configured_s": 10, "deep_onset_s": 180, "cold_onset_s": 300},
        }
    )
    obs = T.execute(ad, {})
    assert obs.observations["honored"] is True
    assert obs.observations["promise_wake_ms"] == 20.0  # warm within the promise
    assert obs.observations["cold_onset_s"] == 300
    res = T.score(obs)
    assert res.measurements["idle_timeout_honored"].value is True
    assert res.unsupported is False


def test_local_sim_end_to_end_promise_broken():
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
