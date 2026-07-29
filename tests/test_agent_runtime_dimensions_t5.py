"""T1.1 / T5.1 / T5.2 against local-sim: measurement dimensions on the 0.2 contract.

No cloud account, no fixed port -- each dimension is exercised through the
deterministic local-sim knobs so both a healthy and a degraded runtime score.
"""
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec


def test_t1_1_startup_latency_reports_cold_and_warm(tmp_path):
    spec = RunSpec("agent-runtime", "T1.1", "local-sim",
                   target={"startup": {"cold_ms": 40, "warm_ms": 2}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    m = rec.measurements
    assert m["cold_start_ms"]["evidence"] == "B"
    # cold knob (40ms) is meaningfully slower than warm (2ms) -> ratio > 1
    assert m["cold_start_ms"]["value"] >= m["warm_start_p50_ms"]["value"]
    assert m["cold_warm_ratio"]["value"] is None or m["cold_warm_ratio"]["value"] >= 1.0


def test_t5_1_reports_usage_measurements(tmp_path):
    rec = execute(RunSpec("agent-runtime", "T5.1", "local-sim"), results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["invocations"]["value"] == 8  # the fixed 8-call plan
    assert "vcpu_hours" in rec.measurements
    # local-sim has no real price -> the reference enricher marks usage uncovered,
    # never invents a number.
    pricing = rec.extensions.get("pricing")
    assert pricing is not None
    assert pricing["cost_usd"] == 0.0
    assert set(pricing["uncovered"]) == {"invocations", "vcpu_hours"}


def test_t5_1_inline_price_yields_cost(tmp_path):
    spec = RunSpec("agent-runtime", "T5.1", "local-sim",
                   target={"pricing": {"per_invocation_usd": 0.01, "per_vcpu_hour_usd": 0}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["cost_usd"]["value"] == round(8 * 0.01, 6)


def test_t5_2_scales_cleanly_under_high_limit(tmp_path):
    spec = RunSpec("agent-runtime", "T5.2", "local-sim",
                   target={"scaling": {"concurrency_limit": 10_000}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["scaling_capability"]["value"] == "supported"
    assert rec.measurements["scales_cleanly"]["value"] is True
    assert rec.measurements["concurrency_knee"]["value"] == "none"


def test_t5_2_knee_when_limit_is_low(tmp_path):
    spec = RunSpec("agent-runtime", "T5.2", "local-sim",
                   target={"scaling": {"concurrency_limit": 2, "overload_penalty_ms": 500}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["scales_cleanly"]["value"] is False
    knee = rec.measurements["concurrency_knee"]["value"]
    assert isinstance(knee, int) and knee > 2
    assert any(f["code"] == "agent_runtime.scaling_knee" for f in rec.findings)
