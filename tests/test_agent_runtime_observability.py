"""T4.3 / T4.4 / T4.5 observability-depth dimensions against local-sim.

Each is exercised through deterministic local-sim knobs so both a complete and a
degraded telemetry story scores, with no cloud account.
"""
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec


def test_t4_3_signals_complete(tmp_path):
    spec = RunSpec("agent-runtime", "T4.3", "local-sim",
                   target={"signals": {"metrics_present": 6, "metrics_expected": 6,
                                       "logs_present": 4, "logs_expected": 4,
                                       "structured_logs": True}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["metrics_completeness"]["value"] == 1.0
    assert rec.measurements["logs_completeness"]["value"] == 1.0
    assert rec.measurements["structured_logs"]["value"] is True
    assert not rec.findings


def test_t4_3_incomplete_signals_flagged(tmp_path):
    spec = RunSpec("agent-runtime", "T4.3", "local-sim",
                   target={"signals": {"metrics_present": 3, "metrics_expected": 6,
                                       "structured_logs": False}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["metrics_completeness"]["value"] == 0.5
    codes = {f["code"] for f in rec.findings}
    assert "agent_runtime.signals_incomplete" in codes
    assert "agent_runtime.logs_unstructured" in codes


def test_t4_4_span_propagation_clean(tmp_path):
    spec = RunSpec("agent-runtime", "T4.4", "local-sim",
                   target={"span_propagation": {"spans": 8, "orphan_spans": 0,
                                                "root_count": 1}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["parent_correctness"]["value"] == 1.0
    assert rec.measurements["orphan_spans"]["value"] == 0
    assert not rec.findings


def test_t4_4_broken_propagation_flagged(tmp_path):
    spec = RunSpec("agent-runtime", "T4.4", "local-sim",
                   target={"span_propagation": {"spans": 8, "orphan_spans": 2,
                                                "root_count": 3}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["orphan_spans"]["value"] == 2
    assert rec.measurements["root_count"]["value"] == 3
    assert any(f["code"] == "agent_runtime.broken_span_propagation" for f in rec.findings)


def test_t4_5_export_latency_lossless(tmp_path):
    spec = RunSpec("agent-runtime", "T4.5", "local-sim",
                   target={"export": {"latency_ms": 250, "dropped_ratio": 0.0}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["export_latency_ms"]["value"] == 250
    assert rec.measurements["dropped_ratio"]["value"] == 0.0
    assert not rec.findings


def test_t4_5_dropped_telemetry_flagged(tmp_path):
    spec = RunSpec("agent-runtime", "T4.5", "local-sim",
                   target={"export": {"latency_ms": 5000, "dropped_ratio": 0.1}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["dropped_ratio"]["value"] == 0.1
    assert any(f["code"] == "agent_runtime.telemetry_dropped" for f in rec.findings)
