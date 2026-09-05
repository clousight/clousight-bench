"""The orchestrator emits an OTel-native trace per run (SDK-produced spans): a
root csbench.run span with a child span per lifecycle stage, exported locally as
flat JSONL and linked from the record by trace_id."""

import glob
import json

from clousight_bench.core.orchestrator import execute
from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    ResultRecord,
    RunInfo,
)
from clousight_bench.core.schema import RunSpec
from clousight_bench.core.tracing import (
    emit_run_trace,
    new_span_id,
    new_trace_id,
)


def _spec():
    return RunSpec("agent-runtime", "suite:stub.ok", "local-sim", target={"recovery": {"mode": "auto-retry"}})


def _record(run_id="run-x", stages=None, timings=None, status="completed"):
    return ResultRecord(
        run=RunInfo(
            run_id=run_id,
            started_at="t0",
            finished_at="t1",
            stages=stages or {"SETUP": "ok", "TEARDOWN": "ok"},
            stage_timings=timings or {"SETUP": 12.0, "TEARDOWN": 4.0},
        ),
        identity=Identity(
            domain="d",
            task_id="t",
            task_revision="1",
            scorer_revision="1",
            adapter="a",
            adapter_status="reference",
            core_version="0.2.0",
        ),
        environment=Environment(region="", mode="local", python_version="3.12.0", os_name="Linux"),
        fingerprints=Fingerprints(benchmark="sha256:a", environment="sha256:b", implementation="sha256:c"),
        status=status,
    )


def _read_spans(tmp_path, trace_id):
    path = tmp_path / "traces" / f"{trace_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_run_emits_a_linked_trace_of_stage_spans(tmp_path):
    rec = execute(_spec(), results_dir=tmp_path)
    trace_id = rec.extensions.get("core", {}).get("trace_id")
    assert trace_id, "the record must link to its trace"

    files = glob.glob(str(tmp_path / "traces" / "*.jsonl"))
    assert len(files) == 1 and trace_id in files[0]
    spans = _read_spans(tmp_path, trace_id)

    root = spans[0]
    assert root["name"] == "csbench.run"
    assert root["parent_span_id"] == ""
    assert root["attributes"]["csbench.run_id"] == rec.run.run_id
    assert root["trace_id"] == trace_id
    # SDK resource rides every line — service identity + run correlation keys.
    assert root["resource"]["service.name"] == "csbench"
    assert root["resource"]["csbench.run_id"] == rec.run.run_id

    stage_spans = spans[1:]
    assert stage_spans, "there must be stage spans"
    assert all(s["parent_span_id"] == root["span_id"] for s in stage_spans)
    assert all(s["trace_id"] == trace_id for s in stage_spans)
    assert {"csbench.stage.SETUP", "csbench.stage.EXECUTE", "csbench.stage.TEARDOWN"} <= {
        s["name"] for s in stage_spans
    }


def test_stage_span_durations_match_the_recorded_timings(tmp_path):
    trace_id = new_trace_id()
    emit_run_trace(_record(), tmp_path, trace_id, 1_000_000, 20_000_000)
    by_name = {s["name"]: s for s in _read_spans(tmp_path, trace_id)}
    setup = by_name["csbench.stage.SETUP"]
    assert setup["end_unix_nano"] - setup["start_unix_nano"] == 12_000_000
    # laid end-to-end: TEARDOWN starts where SETUP ends
    assert by_name["csbench.stage.TEARDOWN"]["start_unix_nano"] == setup["end_unix_nano"]
    assert setup["status"] == "OK"
    assert by_name["csbench.run"]["status"] == "OK"


def test_plugin_exporters_receive_sdk_spans(tmp_path, monkeypatch):
    """Entry-point exporters are OTel SDK SpanExporters — they get ReadableSpans."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from clousight_bench.core import registry as _reg

    sink = InMemorySpanExporter()
    monkeypatch.setattr(_reg, "load_span_exporters", lambda: [sink])
    emit_run_trace(_record(), tmp_path, new_trace_id(), 0, 20_000_000)
    names = {s.name for s in sink.get_finished_spans()}
    assert "csbench.run" in names and "csbench.stage.SETUP" in names


def test_failed_stage_maps_to_error_status(tmp_path):
    trace_id = new_trace_id()
    emit_run_trace(
        _record(stages={"SETUP": "failed"}, timings={"SETUP": 5.0}, status="failed"),
        tmp_path,
        trace_id,
        0,
        5_000_000,
    )
    by_name = {s["name"]: s for s in _read_spans(tmp_path, trace_id)}
    assert by_name["csbench.run"]["status"] == "ERROR"
    assert by_name["csbench.stage.SETUP"]["status"] == "ERROR"


def test_span_ids_are_w3c_shaped():
    assert len(new_trace_id()) == 32  # 128-bit hex
    assert len(new_span_id()) == 16  # 64-bit hex
    int(new_trace_id(), 16)  # parseable hex
