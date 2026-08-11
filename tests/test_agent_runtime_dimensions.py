"""T1.2 / T2.1 / T4.1 / T4.2 against local-sim: support and absence are both findings.

No cloud account, no fixed port -- exercises each new dimension's scoring under
both a capable and a degraded runtime configuration.
"""

from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec


def test_t1_2_durable_state_persists(tmp_path):
    spec = RunSpec("agent-runtime", "T1.2", "local-sim", target={"state_persistence": "durable"})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["state_capability"]["value"] == "supported"
    assert rec.measurements["state_persisted"]["value"] is True
    assert rec.measurements["persistence_mode"]["value"] == "durable"


def test_t1_2_ephemeral_state_lost(tmp_path):
    spec = RunSpec("agent-runtime", "T1.2", "local-sim", target={"state_persistence": "ephemeral"})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["state_persisted"]["value"] is False
    assert rec.measurements["persistence_mode"]["value"] == "ephemeral"


def test_t2_1_all_paths_supported_by_default(tmp_path):
    rec = execute(RunSpec("agent-runtime", "T2.1", "local-sim"), results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["supported_count"]["value"] == 3
    assert rec.measurements["supported_paths"]["value"] == ["mcp", "native", "openapi"]


def test_t2_1_restricted_paths(tmp_path):
    spec = RunSpec("agent-runtime", "T2.1", "local-sim", target={"tool_registration": ["mcp"]})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["mcp"]["value"] is True
    assert rec.measurements["openapi"]["value"] is False
    assert rec.measurements["native"]["value"] is False
    assert rec.measurements["supported_count"]["value"] == 1


def test_t4_1_full_trace_is_complete(tmp_path):
    rec = execute(RunSpec("agent-runtime", "T4.1", "local-sim"), results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["trace_capability"]["value"] == "supported"
    assert rec.measurements["span_completeness"]["value"] == 1.0
    assert rec.measurements["kinds_missing"]["value"] == []


def test_t4_1_partial_trace_drops_tool_spans(tmp_path):
    spec = RunSpec("agent-runtime", "T4.1", "local-sim", target={"trace": {"completeness": "partial"}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.measurements["span_completeness"]["value"] < 1.0
    assert "TOOL" in rec.measurements["kinds_missing"]["value"]


def test_t4_2_otel_export_valid_by_default(tmp_path):
    rec = execute(RunSpec("agent-runtime", "T4.2", "local-sim"), results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["otel_export_supported"]["value"] is True
    assert rec.measurements["otel_valid"]["value"] is True
    assert rec.measurements["span_count"]["value"] >= 1
    assert rec.measurements["problems"]["value"] == []


def test_t4_2_otel_export_unsupported(tmp_path):
    spec = RunSpec("agent-runtime", "T4.2", "local-sim", target={"trace": {"otel_export": False}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "unsupported"
    assert rec.measurements["otel_export_supported"]["value"] is False
    assert rec.measurements["otel_valid"]["value"] is False
