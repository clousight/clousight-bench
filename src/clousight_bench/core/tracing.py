"""OTel-native execution tracing for the bench pipeline — built on the SDK.

This instruments the ORCHESTRATOR — a trace per run, a span per lifecycle stage.
Spans are produced through the OpenTelemetry SDK (a per-run ``TracerProvider``
with a ``csbench`` ``Resource``), so third-party exporters registered under the
``clousight_bench.span_exporters`` entry point receive standard
``ReadableSpan``s and any OTel backend can ingest the run trace unmodified.
The entry-point contract is the SDK's ``SpanExporter`` — the whole exporter
ecosystem (OTLP, vendor backends) plugs in directly.

The bundled :class:`LocalFileSpanExporter` lands each run's spans as queryable
flat JSONL under ``<results>/traces/<trace_id>.jsonl`` (hex ids, nanosecond
times, semconv-named attributes, the resource attached per line) — greppable
now, loadable into any OTel tool later. With the ``[otlp]`` extra installed and
``CLOUSIGHT_OTLP_ENDPOINT`` set, an OTLP/HTTP exporter ships the same spans to
your collector (Jaeger/Tempo/ARMS/X-Ray …).

Stage span durations are the exact measured ``stage_timings``; their absolute
starts are laid in lifecycle order from the run start (the pipeline is
sequential, so this is faithful and needs no extra instrumentation). Telemetry
never breaks a run: every emission path is fail-safe.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.trace import StatusCode

from clousight_bench.core.record import STAGES

if TYPE_CHECKING:
    from clousight_bench.core.record import ResultRecord

logger = logging.getLogger(__name__)

TRACES_DIRNAME = "traces"
OTLP_ENDPOINT_ENV = "CLOUSIGHT_OTLP_ENDPOINT"

# ResultRecord stage status -> OTel status code.
_STATUS = {"ok": StatusCode.OK, "failed": StatusCode.ERROR, "skipped": StatusCode.UNSET}


def new_trace_id() -> str:
    """A 128-bit trace id as 32 hex chars (W3C / OTLP)."""
    return os.urandom(16).hex()


def new_span_id() -> str:
    """A 64-bit span id as 16 hex chars (W3C / OTLP)."""
    return os.urandom(8).hex()


class _PresetTraceIds(RandomIdGenerator):
    """SDK id generator that pins the trace id to the run's preset id.

    The run's trace id is decided at orchestrator start (so a suite's SUT
    trajectory can share it before the trace is emitted at finalize); span ids
    stay random.
    """

    def __init__(self, trace_id_hex: str) -> None:
        self._trace_id = int(trace_id_hex, 16)

    def generate_trace_id(self) -> int:
        return self._trace_id


def flatten_span(span: ReadableSpan) -> dict[str, Any]:
    """One queryable flat dict per span: hex ids, ns times, semconv attributes."""
    ctx = span.get_span_context()
    if ctx is None:  # pragma: no cover - ReadableSpan always carries a context
        raise ValueError("span has no context")
    start = int(span.start_time or 0)
    end = int(span.end_time or start)
    return {
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "parent_span_id": format(span.parent.span_id, "016x") if span.parent else "",
        "name": span.name,
        "start_unix_nano": start,
        "end_unix_nano": end,
        "duration_ms": round((end - start) / 1e6, 3),
        "status": span.status.status_code.name,
        "attributes": dict(span.attributes or {}),
        "resource": dict(span.resource.attributes or {}),
    }


class LocalFileSpanExporter(SpanExporter):
    """Writes a run's spans as flat JSONL to ``<results>/traces/<trace_id>.jsonl``.

    Buffers until shutdown so the file lists the root span first and children in
    start order (one file per trace, i.e. per run).
    """

    name = "local"

    def __init__(self, results_dir: Path | str) -> None:
        self._results_dir = Path(results_dir)
        self._spans: list[ReadableSpan] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self._spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        if not self._spans:
            return
        ordered = sorted(self._spans, key=lambda s: (s.parent is not None, int(s.start_time or 0)))
        head_ctx = ordered[0].get_span_context()
        if head_ctx is None:  # pragma: no cover - ReadableSpan always carries a context
            return
        trace_id = format(head_ctx.trace_id, "032x")
        path = self._results_dir / TRACES_DIRNAME / f"{trace_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(json.dumps(flatten_span(s), ensure_ascii=False, default=str) for s in ordered)
        path.write_text(body + "\n", encoding="utf-8")
        self._spans = []

    def force_flush(self, timeout_millis: int = 30_000) -> bool:  # noqa: ARG002
        return True


def _otlp_exporter() -> SpanExporter | None:
    """The optional OTLP/HTTP exporter, when configured — never a hard failure."""
    endpoint = os.environ.get(OTLP_ENDPOINT_ENV, "").strip()
    if not endpoint:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )
    except Exception:  # noqa: BLE001 - extra not installed
        logger.warning(
            "%s is set but the OTLP exporter is not installed — pip install clousight-bench[otlp]",
            OTLP_ENDPOINT_ENV,
        )
        return None
    try:
        return OTLPSpanExporter(endpoint=endpoint)
    except Exception as exc:  # noqa: BLE001 - misconfig must not fail the run
        logger.warning("OTLP exporter construction failed: %s", exc)
        return None


def _build_provider(record: ResultRecord, results_dir: Path, trace_id: str) -> TracerProvider:
    resource = Resource.create(
        {
            "service.name": "csbench",
            "service.version": record.identity.core_version,
            "csbench.run_id": record.run.run_id,
            "csbench.domain": record.identity.domain,
            "csbench.task_id": record.identity.task_id,
        }
    )
    provider = TracerProvider(resource=resource, id_generator=_PresetTraceIds(trace_id))
    provider.add_span_processor(SimpleSpanProcessor(LocalFileSpanExporter(results_dir)))
    otlp = _otlp_exporter()
    if otlp is not None:
        provider.add_span_processor(SimpleSpanProcessor(otlp))
    from clousight_bench.core.registry import load_span_exporters  # noqa: PLC0415

    for exporter in load_span_exporters():
        try:
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        except Exception as exc:  # noqa: BLE001 - a bad exporter must not fail the run
            logger.warning("span exporter %r rejected: %s", getattr(exporter, "name", "?"), exc)
    return provider


def emit_run_trace(
    record: ResultRecord,
    results_dir: Path,
    trace_id: str,
    root_start_ns: int,
    root_end_ns: int,
) -> None:
    """Emit the run's trace through the SDK: one root ``csbench.run`` span with a
    child ``csbench.stage.<STAGE>`` span per timed stage, laid end-to-end in
    lifecycle order using the measured ``stage_timings`` for exact durations."""
    provider = _build_provider(record, Path(results_dir), trace_id)
    try:
        tracer = provider.get_tracer("clousight_bench")
        root = tracer.start_span(
            "csbench.run",
            start_time=root_start_ns,
            attributes={
                "csbench.run_id": record.run.run_id,
                "csbench.domain": record.identity.domain,
                "csbench.task_id": record.identity.task_id,
                "csbench.adapter": record.identity.adapter,
                "csbench.adapter_status": record.identity.adapter_status,
                "csbench.status": record.status,
            },
        )
        root.set_status(StatusCode.OK if record.status in ("completed", "unsupported") else StatusCode.ERROR)
        parent_ctx = otel_trace.set_span_in_context(root)
        cursor = root_start_ns
        for stage in STAGES:
            duration_ms = record.run.stage_timings.get(stage)
            if duration_ms is None:
                continue
            duration_ns = int(duration_ms * 1_000_000)
            span = tracer.start_span(
                f"csbench.stage.{stage}",
                context=parent_ctx,
                start_time=cursor,
                attributes={"csbench.stage": stage},
            )
            span.set_status(_STATUS.get(record.run.stages.get(stage, ""), StatusCode.UNSET))
            span.end(end_time=cursor + duration_ns)
            cursor += duration_ns
        root.end(end_time=root_end_ns)
    finally:
        provider.shutdown()
