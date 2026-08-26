"""The orchestrator emits an OTel-shaped trace per run: a root csbench.run span
with a child span per lifecycle stage, exported locally as queryable JSONL and
linked from the record by trace_id."""

import glob
import json

from clousight_bench.core.orchestrator import execute
from clousight_bench.core.record import RunInfo
from clousight_bench.core.registry import load_span_exporters
from clousight_bench.core.schema import RunSpec
from clousight_bench.core.tracing import (
    LocalFileSpanExporter,
    Span,
    build_run_trace,
    new_span_id,
    new_trace_id,
)


def _spec():
    return RunSpec("agent-runtime", "stub.ok", "local-sim", target={"recovery": {"mode": "auto-retry"}})


def test_run_emits_a_linked_trace_of_stage_spans(tmp_path):
    rec = execute(_spec(), results_dir=tmp_path)
    trace_id = rec.extensions.get("core", {}).get("trace_id")
    assert trace_id, "the record must link to its trace"

    files = glob.glob(str(tmp_path / "traces" / "*.jsonl"))
    assert len(files) == 1 and trace_id in files[0]
    spans = [json.loads(line) for line in open(files[0], encoding="utf-8")]

    root = spans[0]
    assert root["name"] == "csbench.run"
    assert root["parent_span_id"] == ""
    assert root["attributes"]["run_id"] == rec.run.run_id
    assert root["trace_id"] == trace_id

    stage_spans = spans[1:]
    assert stage_spans, "there must be stage spans"
    assert all(s["parent_span_id"] == root["span_id"] for s in stage_spans)
    assert all(s["trace_id"] == trace_id for s in stage_spans)
    assert {"csbench.stage.SETUP", "csbench.stage.EXECUTE", "csbench.stage.TEARDOWN"} <= {
        s["name"] for s in stage_spans
    }


def test_stage_span_durations_match_the_recorded_timings():
    info = RunInfo(
        run_id="run-x",
        started_at="t0",
        finished_at="t1",
        stages={"SETUP": "ok", "TEARDOWN": "ok"},
        stage_timings={"SETUP": 12.0, "TEARDOWN": 4.0},
    )

    class _Rec:
        run = info

        class identity:
            domain = "d"
            task_id = "t"
            adapter = "a"
            adapter_status = "reference"
            core_version = "0.2.0"

        status = "completed"

    spans = build_run_trace(_Rec(), new_trace_id(), 1_000_000, 2_000_000)
    by_name = {s.name: s for s in spans}
    setup = by_name["csbench.stage.SETUP"]
    assert (setup.end_unix_nano - setup.start_unix_nano) == 12_000_000
    # laid end-to-end: TEARDOWN starts where SETUP ends
    assert by_name["csbench.stage.TEARDOWN"].start_unix_nano == setup.end_unix_nano


def test_local_exporter_is_registered_via_entry_point():
    exporters = load_span_exporters()
    assert any(isinstance(e, LocalFileSpanExporter) for e in exporters)
    assert any(e.name == "local" for e in exporters)


def test_exporter_writes_nothing_for_no_spans(tmp_path):
    LocalFileSpanExporter().export([], tmp_path)
    assert not (tmp_path / "traces").exists()


def test_span_ids_are_otlp_shaped():
    assert len(new_trace_id()) == 32  # 128-bit hex
    assert len(new_span_id()) == 16  # 64-bit hex
    span = Span("n", "t", "s", "", 0, 1_000_000)
    assert span.to_dict()["duration_ms"] == 1.0
