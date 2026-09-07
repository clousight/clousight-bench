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


#: The stage a suite's trajectory belongs under. Everything a suite reports
#: happened while ``task.execute()`` was running, by construction — SCORE is a
#: pure function over what EXECUTE already sealed.
_TRAJECTORY_STAGE = "EXECUTE"


def _replay_trajectory(
    tracer: Any, parent_ctx: Any, stage: str, record: ResultRecord, results_dir: Path
) -> None:
    """Re-emit a suite's own trajectory as children of the stage that ran it.

    The two halves of a run's trace were already the same trace — a suite gets
    the run's ``trace_id`` on ``DriverContext`` — but the suite's root span had
    no parent, so it was a *second* root sitting in a separate file, and the
    viewer showed one or the other. A reader of an official TPC run saw 76 query
    spans and no lifecycle; a reader of a reference run saw the lifecycle and an
    empty EXECUTE.

    Re-emitting here rather than at write time keeps the suite's own artifact
    byte-identical — it is the SUT's unmodified account, and its sha256 is
    pinned in the record. The run trace becomes the merged view; the artifact
    stays the primary source.

    Spans whose timestamps fall outside the stage window are still emitted at
    their own times. Clamping them would hide exactly the disagreement worth
    seeing, and an honest overhang is better than a tidy lie.
    """
    if stage != _TRAJECTORY_STAGE:
        return
    spans = _load_trajectory_spans(record, results_dir)
    if not spans:
        return
    by_id: dict[str, Any] = {}
    # Parents before children: a span whose parent is in the file must nest
    # under the re-emitted parent, not under the stage.
    for span in _in_parent_order(spans):
        start_ns = span.get("start_unix_nano")
        end_ns = span.get("end_unix_nano")
        if not isinstance(start_ns, int) or not isinstance(end_ns, int):
            continue
        parent_span_id = str(span.get("parent_span_id") or "")
        ctx = by_id.get(parent_span_id, parent_ctx)
        attributes = span.get("attributes")
        emitted = tracer.start_span(
            str(span.get("name") or "span"),
            context=ctx,
            start_time=start_ns,
            attributes=dict(attributes) if isinstance(attributes, dict) else None,
        )
        emitted.set_status(StatusCode.ERROR if span.get("status") == "ERROR" else StatusCode.OK)
        span_id = str(span.get("span_id") or "")
        if span_id:
            by_id[span_id] = otel_trace.set_span_in_context(emitted)
        emitted.end(end_time=max(end_ns, start_ns))


def _in_parent_order(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Spans ordered so every parent precedes its children.

    A trajectory file is normally already in that order, but nothing enforces
    it, and emitting a child first would silently re-root it on the stage.
    Spans in a cycle or with a dangling parent come last and land on the stage,
    which is the honest place for a span whose parent we cannot resolve.
    """
    remaining = {str(s.get("span_id") or f"#{i}"): s for i, s in enumerate(spans)}
    ordered: list[dict[str, Any]] = []
    placed: set[str] = set()
    progressed = True
    while remaining and progressed:
        progressed = False
        for span_id in list(remaining):
            parent = str(remaining[span_id].get("parent_span_id") or "")
            if parent == "" or parent in placed or parent not in remaining:
                ordered.append(remaining.pop(span_id))
                placed.add(span_id)
                progressed = True
    ordered.extend(remaining.values())  # cycles: emit them, parented to the stage
    return ordered


def _load_trajectory_spans(record: ResultRecord, results_dir: Path) -> list[dict[str, Any]]:
    """The record's trajectory artifact, parsed. Empty on anything unexpected —
    a trace is telemetry and must never be the reason a run fails."""
    artifact = next(
        (a for a in record.artifacts if isinstance(a, dict) and a.get("kind") == "trajectory"),
        None,
    )
    declared = artifact.get("path") if isinstance(artifact, dict) else None
    if not isinstance(declared, str):
        return []
    root = Path(results_dir).resolve()
    candidate = (Path(results_dir) / "artifacts" / declared).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return []
    spans: list[dict[str, Any]] = []
    try:
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            spans.append(parsed)
    return spans


def emit_run_trace(
    record: ResultRecord,
    results_dir: Path,
    trace_id: str,
    root_start_ns: int,
    root_end_ns: int,
) -> None:
    """Emit the run's trace through the SDK: one root ``csbench.run`` span with a
    child ``csbench.stage.<STAGE>`` span per timed stage.

    Stages are placed at the **real** offsets in ``run.stage_spans``, so a gap
    between two stages shows as a gap and anything with its own clock — a
    suite's trajectory spans — lands inside the stage that produced it.

    Records written before 0.6.1 carry no ``stage_spans``; those fall back to
    the old behaviour of laying the stages end-to-end from ``stage_timings``,
    which gets every duration right and every start time wrong. The fallback is
    kept so an old record still renders, not because it was correct.
    """
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
        windows = record.run.stage_spans
        stage_ctx: dict[str, Any] = {}
        for stage in STAGES:
            duration_ms = record.run.stage_timings.get(stage)
            if duration_ms is None:
                continue
            window = windows.get(stage)
            if window is not None:
                start_ns = root_start_ns + int(window[0] * 1_000_000)
                end_ns = root_start_ns + int(window[1] * 1_000_000)
            else:  # pre-0.6.1 record: durations only, so lay them end-to-end
                start_ns = cursor
                end_ns = cursor + int(duration_ms * 1_000_000)
            span = tracer.start_span(
                f"csbench.stage.{stage}",
                context=parent_ctx,
                start_time=start_ns,
                attributes={"csbench.stage": stage},
            )
            span.set_status(_STATUS.get(record.run.stages.get(stage, ""), StatusCode.UNSET))
            stage_ctx[stage] = otel_trace.set_span_in_context(span)
            _replay_trajectory(tracer, stage_ctx[stage], stage, record, results_dir)
            span.end(end_time=end_ns)
            cursor = end_ns
        root.end(end_time=root_end_ns)
    finally:
        provider.shutdown()
