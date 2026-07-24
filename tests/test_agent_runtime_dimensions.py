"""T1.2 / T2.1 / T4.1 / T4.2 against local-sim: support and absence are both findings.

No cloud account, no fixed port -- exercises each new dimension's scoring under
both a capable and a degraded runtime configuration.
"""
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec


def test_t1_2_durable_state_persists(tmp_path):
    spec = RunSpec("agent-runtime", "T1.2", "local-sim",
                   target={"state_persistence": "durable"})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.ok
    assert rec.metrics["state_capability"] == "supported"
    assert rec.metrics["state_persisted"] is True
    assert rec.metrics["persistence_mode"] == "durable"


def test_t1_2_ephemeral_state_lost(tmp_path):
    spec = RunSpec("agent-runtime", "T1.2", "local-sim",
                   target={"state_persistence": "ephemeral"})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.ok
    assert rec.metrics["state_persisted"] is False
    assert rec.metrics["persistence_mode"] == "ephemeral"


def test_t2_1_all_paths_supported_by_default(tmp_path):
    rec = execute(RunSpec("agent-runtime", "T2.1", "local-sim"), results_dir=tmp_path)
    assert rec.ok
    assert rec.metrics["supported_count"] == 3
    assert rec.metrics["supported_paths"] == ["mcp", "native", "openapi"]


def test_t2_1_restricted_paths(tmp_path):
    spec = RunSpec("agent-runtime", "T2.1", "local-sim",
                   target={"tool_registration": ["mcp"]})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.metrics["mcp"] is True
    assert rec.metrics["openapi"] is False
    assert rec.metrics["native"] is False
    assert rec.metrics["supported_count"] == 1


def test_t4_1_full_trace_is_complete(tmp_path):
    rec = execute(RunSpec("agent-runtime", "T4.1", "local-sim"), results_dir=tmp_path)
    assert rec.ok
    assert rec.metrics["trace_capability"] == "supported"
    assert rec.metrics["span_completeness"] == 1.0
    assert rec.metrics["kinds_missing"] == []


def test_t4_1_partial_trace_drops_tool_spans(tmp_path):
    spec = RunSpec("agent-runtime", "T4.1", "local-sim",
                   target={"trace": {"completeness": "partial"}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.metrics["span_completeness"] < 1.0
    assert "TOOL" in rec.metrics["kinds_missing"]


def test_t4_2_otel_export_valid_by_default(tmp_path):
    rec = execute(RunSpec("agent-runtime", "T4.2", "local-sim"), results_dir=tmp_path)
    assert rec.ok
    assert rec.metrics["otel_export_supported"] is True
    assert rec.metrics["otel_valid"] is True
    assert rec.metrics["span_count"] >= 1
    assert rec.metrics["problems"] == []


def test_t4_2_otel_export_unsupported(tmp_path):
    spec = RunSpec("agent-runtime", "T4.2", "local-sim",
                   target={"trace": {"otel_export": False}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.metrics["otel_export_supported"] is False
    assert rec.metrics["otel_valid"] is False
