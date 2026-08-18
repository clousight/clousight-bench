from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime import AgentRuntimeDomain
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter
from clousight_bench.domains.agent_runtime.tasks.t1_13_startup_curve import StartupCurveTask


def _supported(**over):
    obs = {
        "capability": "supported",
        "curve_ms": [520.0, 20.0, 20.0, 20.0, 20.0],
        "cold_start_ms": 520.0,
        "second_call_ms": 20.0,
        "third_call_ms": 20.0,
        "warm_steady_ms": 20.0,
        "speedup_ratio": 26.0,
        "warmed_after_n_calls": 2,
        "reuse_reliable": True,
        "errors": 0,
        "n_calls": 5,
    }
    obs.update(over)
    return ObservationBundle(observations=obs)


def test_score_supported_reports_cold_warm_and_speedup():
    res = StartupCurveTask().score(_supported())
    m = res.measurements
    assert m["startup_curve_capability"].value == "supported"
    assert m["cold_start_ms"].value == 520.0
    assert m["warm_steady_ms"].value == 20.0
    assert m["cold_warm_speedup"].value == 26.0
    assert m["warmed_after_n_calls"].value == 2
    assert m["reuse_reliable"].value is True
    assert not res.findings  # reliable + warmed → no findings
    assert "cold=520.0ms" in res.notes


def test_score_unreliable_emits_warning():
    res = StartupCurveTask().score(_supported(reuse_reliable=False, errors=2))
    codes = {f.code: f.severity for f in res.findings}
    assert codes.get("agent_runtime.startup_reuse_unreliable") == "warning"


def test_score_never_warmed_emits_info():
    res = StartupCurveTask().score(
        _supported(warmed_after_n_calls=None, warm_steady_ms=None, speedup_ratio=None)
    )
    codes = {f.code for f in res.findings}
    assert "agent_runtime.startup_never_warmed" in codes


def test_score_unsupported_marks_task_unsupported():
    res = StartupCurveTask().score(
        ObservationBundle(observations={"capability": "unsupported", "reason": "no probe"})
    )
    assert res.unsupported is True
    assert res.measurements["startup_curve_capability"].value == "unsupported"
    assert any(f.code == "agent_runtime.startup_curve_probe_absent" for f in res.findings)


def test_local_sim_end_to_end():
    T = AgentRuntimeDomain().tasks()["T1.13"]()
    ad = LocalSimAdapter({"mode": "mock", "startup": {"cold_ms": 500, "warm_ms": 20}})
    obs = T.execute(ad, {})
    # cold = cold_ms + warm_ms, warm = warm_ms; deterministic from the knob.
    assert obs.observations["curve_ms"][0] == 520.0
    assert obs.observations["warm_steady_ms"] == 20.0
    res = T.score(obs)
    assert res.measurements["cold_warm_speedup"].value == 26.0
    assert res.unsupported is False


def test_task_registered_in_domain():
    assert "T1.13" in AgentRuntimeDomain().tasks()
