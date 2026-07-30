"""OTel-shaped execution tracing for the bench pipeline itself.

This instruments the ORCHESTRATOR -- a trace per run, a span per lifecycle stage
-- not the system under test (the SUT's own traces are the agent-runtime
domain's concern). Spans follow the OpenTelemetry data model (trace/span ids,
nanosecond start/end, status, attributes) so any OTLP backend can ingest them,
but core carries NO opentelemetry dependency: it emits plain :class:`Span`
records and hands them to pluggable :class:`SpanExporter` plugins (entry point
``clousight_bench.span_exporters``).

The bundled :class:`LocalFileSpanExporter` lands each run's spans as queryable
JSONL under ``<results>/traces/``; a commercial pack can register a remote OTLP
exporter through the same seam without touching core.

Stage span durations are the exact measured ``stage_timings``; their absolute
starts are laid in lifecycle order from the run start (the pipeline is
sequential, so this is faithful and needs no extra instrumentation).
"""
from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clousight_bench.core.record import STAGES

if TYPE_CHECKING:
    from clousight_bench.core.record import ResultRecord

logger = logging.getLogger(__name__)

TRACES_DIRNAME = "traces"

# ResultRecord stage status -> OTel span status code.
_STATUS = {"ok": "OK", "failed": "ERROR", "skipped": "UNSET"}


def new_trace_id() -> str:
    """A 128-bit trace id as 32 hex chars (OTLP)."""
    return os.urandom(16).hex()


def new_span_id() -> str:
    """A 64-bit span id as 16 hex chars (OTLP)."""
    return os.urandom(8).hex()


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str
    start_unix_nano: int
    end_unix_nano: int
    status: str = "UNSET"  # OK | ERROR | UNSET
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "start_unix_nano": self.start_unix_nano,
            "end_unix_nano": self.end_unix_nano,
            "duration_ms": round((self.end_unix_nano - self.start_unix_nano) / 1e6, 3),
            "status": self.status,
            "attributes": dict(self.attributes),
        }


class SpanExporter(ABC):
    """Ships one run's spans somewhere.

    Registered via the ``clousight_bench.span_exporters`` entry point; open-core
    ships the local file exporter, a commercial pack can add a remote OTLP one."""

    name: str = "abstract"

    @abstractmethod
    def export(self, spans: list[Span], results_dir: Path) -> None: ...


class LocalFileSpanExporter(SpanExporter):
    """Writes a run's spans as OTLP-shaped JSONL to ``<results>/traces/<trace_id>.jsonl``.

    Greppable / jq-able immediately, and trivially loadable into any OTel tool or
    a columnar store later. One file per trace (i.e. per run)."""

    name = "local"

    def export(self, spans: list[Span], results_dir: Path) -> None:
        if not spans:
            return
        path = Path(results_dir) / TRACES_DIRNAME / f"{spans[0].trace_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(json.dumps(s.to_dict(), ensure_ascii=False) for s in spans)
        path.write_text(body + "\n", encoding="utf-8")


def build_run_trace(
    record: ResultRecord, trace_id: str, root_start_ns: int, root_end_ns: int
) -> list[Span]:
    """One root ``csbench.run`` span with a child ``csbench.stage.<STAGE>`` span
    per timed stage, laid end-to-end in lifecycle order using the measured
    ``stage_timings`` for exact durations."""
    root_id = new_span_id()
    run_status = "OK" if record.status in ("completed", "unsupported") else "ERROR"
    root = Span(
        name="csbench.run",
        trace_id=trace_id,
        span_id=root_id,
        parent_span_id="",
        start_unix_nano=root_start_ns,
        end_unix_nano=root_end_ns,
        status=run_status,
        attributes={
            "run_id": record.run.run_id,
            "domain": record.identity.domain,
            "task_id": record.identity.task_id,
            "adapter": record.identity.adapter,
            "adapter_status": record.identity.adapter_status,
            "core_version": record.identity.core_version,
            "status": record.status,
        },
    )
    spans = [root]
    cursor = root_start_ns
    for stage in STAGES:
        duration_ms = record.run.stage_timings.get(stage)
        if duration_ms is None:
            continue
        duration_ns = int(duration_ms * 1_000_000)
        spans.append(
            Span(
                name=f"csbench.stage.{stage}",
                trace_id=trace_id,
                span_id=new_span_id(),
                parent_span_id=root_id,
                start_unix_nano=cursor,
                end_unix_nano=cursor + duration_ns,
                status=_STATUS.get(record.run.stages.get(stage, ""), "UNSET"),
                attributes={"stage": stage},
            )
        )
        cursor += duration_ns
    return spans


def export_trace(results_dir: Path, spans: list[Span]) -> None:
    """Hand the spans to every registered exporter. Telemetry never breaks a run:
    an exporter that raises is logged and skipped."""
    from clousight_bench.core.registry import load_span_exporters

    for exporter in load_span_exporters():
        try:
            exporter.export(spans, Path(results_dir))
        except Exception as exc:  # noqa: BLE001 - a bad exporter must not fail the run
            logger.warning("span exporter %r failed: %s", getattr(exporter, "name", "?"), exc)
