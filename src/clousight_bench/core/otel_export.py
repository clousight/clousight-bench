"""One-shot OTel metrics + logs export of a finished run's record.

With ``CLOUSIGHT_OTLP_ENDPOINT`` set and the ``[otlp]`` extra installed, the
run's numeric measurements ship as OTel **gauges** (attributes: unit,
reproducibility_class, official) and its errors/findings as OTel **log
records** — alongside the trace signal that ``core/tracing.py`` already emits.
One trace-correlated snapshot per run, consumable by any collector backend.

Fail-safe by contract: any exporter/SDK problem is logged and swallowed —
telemetry can never fail a run. Endpoint handling: the env value may be the
collector base (``http://host:4318``) or a full ``/v1/traces`` URL; the
per-signal paths (``/v1/metrics``, ``/v1/logs``) are derived either way.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from clousight_bench.core.tracing import OTLP_ENDPOINT_ENV

if TYPE_CHECKING:
    from clousight_bench.core.record import ResultRecord

logger = logging.getLogger(__name__)

_SEVERITY = {"info": 9, "warning": 13, "critical": 21}  # OTel INFO/WARN/FATAL


def signal_endpoint(env_value: str, signal: str) -> str:
    """Derive the per-signal OTLP/HTTP endpoint from the configured value."""
    base = env_value.rstrip("/")
    for suffix in ("/v1/traces", "/v1/metrics", "/v1/logs"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return f"{base}/v1/{signal}"


def export_record_signals(record: ResultRecord, trace_id: str) -> None:
    """Ship the record's measurements (gauges) + errors/findings (logs). Fail-safe."""
    endpoint = os.environ.get(OTLP_ENDPOINT_ENV, "").strip()
    if not endpoint:
        return
    try:
        _export_metrics(record, trace_id, endpoint)
    except Exception as exc:  # noqa: BLE001 - telemetry never fails a run
        logger.warning("run %s: OTel metrics export failed: %s", record.run.run_id, exc)
    try:
        _export_logs(record, trace_id, endpoint)
    except Exception as exc:  # noqa: BLE001
        logger.warning("run %s: OTel logs export failed: %s", record.run.run_id, exc)


def _resource(record: ResultRecord) -> Any:
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415

    return Resource.create(
        {
            "service.name": "csbench",
            "csbench.run_id": record.run.run_id,
            "csbench.domain": record.identity.domain,
            "csbench.task_id": record.identity.task_id,
        }
    )


def record_gauges(record: ResultRecord, trace_id: str, meter: Any) -> int:
    """Record one gauge point per numeric measurement on *meter*; returns count."""
    emitted = 0
    for key, entry in (record.measurements or {}).items():
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        gauge = meter.create_gauge(name=f"csbench.{key}")
        gauge.set(
            value,
            attributes={
                "csbench.unit": str(entry.get("unit", "")),
                "csbench.reproducibility_class": str(entry.get("reproducibility_class", "")),
                "csbench.official": bool(entry.get("official", False)),
                "csbench.trace_id": trace_id,
            },
        )
        emitted += 1
    return emitted


def _export_metrics(record: ResultRecord, trace_id: str, endpoint: str) -> None:
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (  # noqa: PLC0415
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider  # noqa: PLC0415
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader  # noqa: PLC0415

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=signal_endpoint(endpoint, "metrics"), timeout=5),
        export_interval_millis=3_600_000,  # one-shot: flushed by shutdown below
    )
    provider = MeterProvider(resource=_resource(record), metric_readers=[reader])
    try:
        record_gauges(record, trace_id, provider.get_meter("clousight_bench"))
    finally:
        provider.shutdown()


def record_logs(record: ResultRecord, trace_id: str, otel_logger: Any) -> int:
    """Emit one log record per error + finding on *otel_logger*; returns count."""
    emitted = 0
    for err in record.errors or []:
        if not isinstance(err, dict):
            continue
        _emit_log(
            otel_logger,
            severity=17,  # ERROR
            body=str(err.get("message", "")),
            # trace correlation is attribute-only for now: the SDK LogRecord's
            # native trace_id field needs a live SpanContext, which this
            # post-run snapshot does not have.
            attributes={
                "csbench.stage": str(err.get("stage", "")),
                "csbench.code": str(err.get("code", "")),
                "csbench.trace_id": trace_id,
            },
        )
        emitted += 1
    for finding in record.findings or []:
        entry = finding if isinstance(finding, dict) else {}
        _emit_log(
            otel_logger,
            severity=_SEVERITY.get(str(entry.get("severity", "info")), 9),
            body=str(entry.get("summary", "")),
            attributes={
                "csbench.finding_code": str(entry.get("code", "")),
                "csbench.trace_id": trace_id,
            },
        )
        emitted += 1
    return emitted


def _emit_log(otel_logger: Any, *, severity: int, body: str, attributes: dict[str, Any]) -> None:
    from opentelemetry._logs import LogRecord, SeverityNumber  # noqa: PLC0415

    otel_logger.emit(
        LogRecord(
            severity_number=SeverityNumber(severity),
            body=body,
            attributes=attributes,
        )
    )


def _export_logs(record: ResultRecord, trace_id: str, endpoint: str) -> None:
    from opentelemetry.exporter.otlp.proto.http._log_exporter import (  # noqa: PLC0415
        OTLPLogExporter,
    )
    from opentelemetry.sdk._logs import LoggerProvider  # noqa: PLC0415
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor  # noqa: PLC0415

    provider = LoggerProvider(resource=_resource(record))
    provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=signal_endpoint(endpoint, "logs"), timeout=5))
    )
    try:
        record_logs(record, trace_id, provider.get_logger("clousight_bench"))
    finally:
        provider.shutdown()
