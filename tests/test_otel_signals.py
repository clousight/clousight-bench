"""OTLP ingest + one-shot metrics/logs export of a finished record."""

from __future__ import annotations

import json

import pytest

from clousight_bench.core.otel_export import record_gauges, record_logs, signal_endpoint
from clousight_bench.core.otel_ingest import ingest_file, ingest_otlp_json
from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    ResultRecord,
    RunInfo,
)

_TRACE = "d" * 32

_OTLP = {
    "resourceSpans": [
        {
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "sut"}}]},
            "scopeSpans": [
                {
                    "spans": [
                        {
                            "traceId": "A" * 32,
                            "spanId": "b" * 16,
                            "parentSpanId": "",
                            "name": "GET /health",
                            "startTimeUnixNano": "1000",
                            "endTimeUnixNano": "2000",
                            "status": {"code": 1},
                            "attributes": [{"key": "http.request.method", "value": {"stringValue": "GET"}}],
                        }
                    ]
                }
            ],
        }
    ]
}


def _record():
    return ResultRecord(
        run=RunInfo(run_id="r", started_at="t0", finished_at="t1", stages={}),
        identity=Identity(
            domain="llm",
            task_id="suite:mmlu",
            task_revision="1",
            scorer_revision="1",
            adapter="llm-endpoint",
            adapter_status="experimental",
            core_version="0.6.0",
        ),
        environment=Environment(region="", mode="cloud", python_version="3.12", os_name="Linux"),
        fingerprints=Fingerprints(benchmark="sha256:a", environment="sha256:b", implementation="sha256:c"),
        status="completed",
        measurements={
            "mmlu.accuracy": {"value": 0.81, "unit": "ratio", "reproducibility_class": "deterministic"},
            "mmlu.notes": {"value": "text", "unit": ""},  # non-numeric -> skipped
        },
        errors=[{"stage": "EXECUTE", "code": "boom", "message": "kaput"}],
        findings=[{"code": "x.y", "severity": "warning", "summary": "watch out"}],
    )


def test_ingest_otlp_json_produces_v3():
    spans = ingest_otlp_json(_OTLP)
    assert len(spans) == 1
    s = spans[0]
    assert s["trace_id"] == "a" * 32  # lowercased
    assert s["status"] == "OK"
    assert s["attributes"]["http.request.method"] == "GET"
    assert s["attributes"]["csbench.ingested"] == "otlp"  # no semconv discriminator
    assert s["resource"]["service.name"] == "sut"


def test_ingest_file_flat_jsonl_and_loud_failure(tmp_path):
    good = {
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
        "parent_span_id": "",
        "name": "n",
        "start_unix_nano": 1,
        "end_unix_nano": 2,
        "status": "OK",
        "attributes": {"db.system.name": "redis"},
    }
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps(good) + "\n")
    assert len(ingest_file(p)) == 1
    p.write_text("not json\n")
    with pytest.raises(ValueError, match="not JSON"):
        ingest_file(p)


def test_signal_endpoint_derivation():
    assert signal_endpoint("http://c:4318", "metrics") == "http://c:4318/v1/metrics"
    assert signal_endpoint("http://c:4318/v1/traces", "logs") == "http://c:4318/v1/logs"


def test_record_gauges_via_in_memory_reader():
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    n = record_gauges(_record(), _TRACE, provider.get_meter("t"))
    assert n == 1  # the non-numeric measurement is skipped
    data = reader.get_metrics_data()
    metrics = [m for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics]
    assert metrics[0].name == "csbench.mmlu.accuracy"
    point = list(metrics[0].data.data_points)[0]
    assert point.value == 0.81
    assert point.attributes["csbench.trace_id"] == _TRACE


def test_record_logs_via_in_memory_exporter():
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import InMemoryLogExporter, SimpleLogRecordProcessor

    sink = InMemoryLogExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(sink))
    n = record_logs(_record(), _TRACE, provider.get_logger("t"))
    provider.shutdown()
    assert n == 2
    bodies = [str(r.log_record.body) for r in sink.get_finished_logs()]
    assert "kaput" in bodies and "watch out" in bodies


def test_cli_trace_import_roundtrip(tmp_path, capsys):
    from clousight_bench.cli.app import main

    src = tmp_path / "otlp.json"
    src.write_text(json.dumps(_OTLP))
    rc = main(["trace", "import", str(src)])
    out = capsys.readouterr().out
    assert rc == 0 and "imported 1 spans" in out
    produced = tmp_path / "otlp.v3.jsonl"
    line = json.loads(produced.read_text().splitlines()[0])
    assert line["trace_id"] == "a" * 32


def test_cli_trace_import_rejects_garbage(tmp_path, capsys):
    from clousight_bench.cli.app import main

    src = tmp_path / "bad.jsonl"
    src.write_text("nope\n")
    rc = main(["trace", "import", str(src)])
    assert rc == 2
    assert "cannot import" in capsys.readouterr().err


def test_ingest_rejects_non_dict_status_and_v2_lines(tmp_path):
    otlp = json.loads(json.dumps(_OTLP))
    otlp["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["status"] = "OK"  # non-dict
    spans = ingest_otlp_json(otlp)  # guard: no raise, UNSET fallback
    assert spans[0]["status"] == "UNSET"

    v2 = {
        "span_id": "s1",
        "trace_id": "t1",
        "parent_id": None,
        "name": "n",
        "kind": "llm_call",
        "t_start": 1.0,
        "t_end": 2.0,
        "status": "ok",
        "attrs": {},
    }
    p = tmp_path / "v2.jsonl"
    p.write_text(json.dumps(v2) + "\n")
    with pytest.raises(ValueError, match="must be v3"):
        ingest_file(p)
