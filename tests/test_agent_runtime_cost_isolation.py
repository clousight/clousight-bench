"""T5.3 / T5.4 / T6.1 cost & isolation dimensions against local-sim.

Deterministic local-sim knobs so both a strong and a weak profile scores, no
cloud account.
"""
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec


def test_t5_3_scale_to_zero_no_idle_bill(tmp_path):
    spec = RunSpec("agent-runtime", "T5.3", "local-sim",
                   target={"idle": {"scales_to_zero": True, "cost_per_hour": 0.0}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["scales_to_zero"]["value"] is True
    assert rec.measurements["idle_cost_per_hour"]["value"] == 0.0
    assert not rec.findings


def test_t5_3_always_on_idle_bill_flagged(tmp_path):
    spec = RunSpec("agent-runtime", "T5.3", "local-sim",
                   target={"idle": {"scales_to_zero": False, "cost_per_hour": 0.05}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["scales_to_zero"]["value"] is False
    assert rec.measurements["idle_cost_per_hour"]["value"] == 0.05
    assert any(f["code"] == "agent_runtime.no_scale_to_zero" for f in rec.findings)


def test_t5_4_concurrency_ceiling(tmp_path):
    spec = RunSpec("agent-runtime", "T5.4", "local-sim",
                   target={"ceiling": {"max_in_flight": 200, "hard_limit": True}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["max_in_flight"]["value"] == 200
    assert rec.measurements["hard_limit"]["value"] is True


def test_t6_1_full_isolation(tmp_path):
    spec = RunSpec("agent-runtime", "T6.1", "local-sim",
                   target={"isolation": {"tenant_isolated": True,
                                         "network_egress_controlled": True,
                                         "filesystem_isolated": True}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["isolation_score"]["value"] == 3
    assert not rec.findings


def test_t6_1_weak_isolation_flagged(tmp_path):
    spec = RunSpec("agent-runtime", "T6.1", "local-sim",
                   target={"isolation": {"tenant_isolated": True,
                                         "network_egress_controlled": False,
                                         "filesystem_isolated": False}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["isolation_score"]["value"] == 1
    assert any(f["code"] == "agent_runtime.weak_isolation" for f in rec.findings)
