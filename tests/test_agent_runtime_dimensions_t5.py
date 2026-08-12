"""T1.1 / T5.1 / T5.2 against local-sim: measurement dimensions on the 0.2 contract.

No cloud account, no fixed port -- each dimension is exercised through the
deterministic local-sim knobs so both a healthy and a degraded runtime score.
"""

from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec


def test_t1_1_startup_latency_reports_cold_and_warm(tmp_path):
    spec = RunSpec("agent-runtime", "T1.1", "local-sim", target={"startup": {"cold_ms": 40, "warm_ms": 2}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    m = rec.measurements
    assert m["cold_start_ms"]["evidence"] == "B"
    # cold knob (40ms) is meaningfully slower than warm (2ms) -> ratio > 1
    assert m["cold_start_ms"]["value"] >= m["warm_start_p50_ms"]["value"]
    assert m["cold_warm_ratio"]["value"] is None or m["cold_warm_ratio"]["value"] >= 1.0


def test_t1_4_sustained_load_reports_throughput_and_tail(tmp_path):
    spec = RunSpec(
        "agent-runtime",
        "T1.4",
        "local-sim",
        target={"load": {"sustained_rps": 40, "base_ms": 35, "tail_ms": 120}},
    )
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    m = rec.measurements
    assert m["load_capability"]["value"] == "supported"
    # target is 50 rps (task constant) but the runtime only sustains 40 -> throttled
    assert m["throughput_rps"]["value"] == 40
    assert m["p50_ms"]["value"] == 35 and m["p99_ms"]["value"] == 155
    assert m["jitter_ms"]["value"] == 120
    # 50 target vs 40 sustained -> ~20% overflow becomes errors
    assert m["error_rate_under_load"]["value"] > 0
    assert any(f["code"] == "agent_runtime.load_errors" for f in rec.findings)


def test_t1_4_no_errors_when_runtime_outpaces_demand(tmp_path):
    spec = RunSpec(
        "agent-runtime",
        "T1.4",
        "local-sim",
        target={"load": {"sustained_rps": 500, "base_ms": 10, "tail_ms": 5}},
    )
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["throughput_rps"]["value"] == 50  # capped at target
    assert rec.measurements["error_rate_under_load"]["value"] == 0


def test_t1_5_warm_retention_reports_keepalive_window(tmp_path):
    spec = RunSpec(
        "agent-runtime", "T1.5", "local-sim", target={"warm": {"retention_ms": 300000, "keeps_warm": True}}
    )
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["warm_retention_ms"]["value"] == 300000
    assert rec.measurements["keeps_warm"]["value"] is True


def test_t1_5_no_warm_pool_is_flagged(tmp_path):
    spec = RunSpec(
        "agent-runtime", "T1.5", "local-sim", target={"warm": {"retention_ms": 0, "keeps_warm": False}}
    )
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["keeps_warm"]["value"] is False
    assert any(f["code"] == "agent_runtime.no_warm_pool" for f in rec.findings)


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


def test_t5_1_reports_usage_only_cost_from_enricher(tmp_path):
    # T5.1 is usage-only now (single cost authority = the pricing enricher).
    spec = RunSpec("agent-runtime", "T5.1", "local-sim")
    rec = execute(spec, results_dir=tmp_path)
    assert "cost_usd" not in rec.measurements
    assert rec.measurements["invocations"]["value"] == 8
    # local-sim has no list price -> enricher covers nothing, cost 0, units uncovered.
    pricing = rec.extensions["pricing"]
    assert pricing["cost_usd"] == 0.0
    assert "invocations" in pricing["uncovered"]


def test_t5_2_scales_cleanly_under_high_limit(tmp_path):
    spec = RunSpec("agent-runtime", "T5.2", "local-sim", target={"scaling": {"concurrency_limit": 10_000}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["scaling_capability"]["value"] == "supported"
    assert rec.measurements["scales_cleanly"]["value"] is True
    assert rec.measurements["concurrency_knee"]["value"] == "none"


def test_t5_2_knee_when_limit_is_low(tmp_path):
    spec = RunSpec(
        "agent-runtime",
        "T5.2",
        "local-sim",
        target={"scaling": {"concurrency_limit": 2, "overload_penalty_ms": 500}},
    )
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["scales_cleanly"]["value"] is False
    knee = rec.measurements["concurrency_knee"]["value"]
    assert isinstance(knee, int) and knee > 2
    assert any(f["code"] == "agent_runtime.scaling_knee" for f in rec.findings)
