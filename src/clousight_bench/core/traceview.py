"""Read and render the execution traces the local exporter writes.

Backs ``csbench trace list`` / ``csbench trace show``: it reads the OTLP-shaped
JSONL under ``<results>/traces/`` -- a quick built-in look, no external tool
needed (the same files also load into any OpenTelemetry viewer).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clousight_bench.core.tracing import TRACES_DIRNAME


def _read_spans(path: Path) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return spans
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            spans.append(json.loads(line))
        except ValueError:
            continue
    return spans


def _root(spans: list[dict[str, Any]]) -> dict[str, Any] | None:
    for span in spans:
        if span.get("parent_span_id") == "" and span.get("name") == "csbench.run":
            return span
    return spans[0] if spans else None


def iter_traces(results_dir: Path) -> list[tuple[Path, list[dict[str, Any]]]]:
    directory = Path(results_dir) / TRACES_DIRNAME
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.jsonl")):
        spans = _read_spans(path)
        if spans:
            out.append((path, spans))
    return out


def trace_summaries(results_dir: Path) -> list[dict[str, Any]]:
    """One row per trace: the root run span's identity + total duration."""
    summaries = []
    for _path, spans in iter_traces(results_dir):
        root = _root(spans)
        if not root:
            continue
        attrs = root.get("attributes", {})
        summaries.append(
            {
                "trace_id": root.get("trace_id", ""),
                "run_id": attrs.get("csbench.run_id", ""),
                "task_id": attrs.get("csbench.task_id", ""),
                "adapter": attrs.get("csbench.adapter", ""),
                "status": attrs.get("csbench.status", ""),
                "duration_ms": root.get("duration_ms", 0.0),
                "spans": len(spans),
            }
        )
    return summaries


def find_trace(results_dir: Path, key: str) -> list[dict[str, Any]] | None:
    """Look a trace up by trace_id (the filename) or by run_id."""
    for path, spans in iter_traces(results_dir):
        root = _root(spans) or {}
        trace_id = root.get("trace_id", "")
        run_id = root.get("attributes", {}).get("csbench.run_id", "")
        if key in (trace_id, run_id, path.stem):
            return spans
    return None


def render_list(summaries: list[dict[str, Any]], sort: str = "started") -> str:
    if not summaries:
        return "no traces found"
    rows = list(summaries)
    if sort == "duration":
        rows.sort(key=lambda r: -r["duration_ms"])
    header = f"{'run_id':<26} {'task':<8} {'adapter':<16} {'status':<11} {'total_ms':>10}  trace"
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r['run_id']:<26} {r['task_id']:<8} {r['adapter']:<16} "
            f"{r['status']:<11} {r['duration_ms']:>10.3f}  {r['trace_id'][:12]}"
        )
    return "\n".join(lines)


#: How deep `csbench trace show` descends by default. One level is the eleven
#: lifecycle stages; two reaches a benchmark's phases; a TPC official run goes
#: four deep and ~900 spans wide, which is a waterfall's job, not a terminal's.
DEFAULT_DEPTH = 2


def render_show(spans: list[dict[str, Any]], depth: int = DEFAULT_DEPTH) -> str:
    """The run's tree with durations + status, flagging the slowest and failures.

    Descends ``depth`` levels. Since the benchmark's own spans are merged into
    the run trace, stopping at the stages would print strictly less than the
    trace holds — but printing all of it would bury the shape under hundreds of
    query rows, so a subtree past the limit is summarised by its count rather
    than dropped silently.
    """
    root = _root(spans)
    if not root:
        return "empty trace"
    attrs = root.get("attributes", {})
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for span in spans:
        if span is root:
            continue
        by_parent.setdefault(str(span.get("parent_span_id") or ""), []).append(span)
    for kids in by_parent.values():
        kids.sort(key=lambda s: s.get("start_unix_nano", 0))

    lines = [
        (
            f"{root.get('name', 'csbench.run')}  {attrs.get('csbench.run_id', '')} "
            f"[{attrs.get('csbench.status', '')}]  (total {root.get('duration_ms', 0.0):.3f} ms, "
            f"{len(spans)} spans)"
        )
    ]

    def _descendants(span_id: str) -> int:
        total = 0
        for child in by_parent.get(span_id, []):
            total += 1 + _descendants(str(child.get("span_id") or ""))
        return total

    def _walk(parent_id: str, level: int, prefix: str) -> None:
        children = by_parent.get(parent_id, [])
        slowest = max((c.get("duration_ms", 0.0) for c in children), default=0.0)
        for i, span in enumerate(children):
            last = i == len(children) - 1
            branch = "└─" if last else "├─"
            name = span.get("attributes", {}).get("csbench.stage") or span.get("name", "")
            duration = span.get("duration_ms", 0.0)
            status = span.get("status", "")
            flags = []
            if status == "ERROR":
                flags.append("FAILED")
            if slowest > 0 and duration >= slowest and len(children) > 1:
                flags.append("slowest")
            suffix = f"  <- {', '.join(flags)}" if flags else ""
            lines.append(f"{prefix}{branch} {name:<24} {duration:>10.3f} ms  {status}{suffix}")

            span_id = str(span.get("span_id") or "")
            hidden = _descendants(span_id)
            # Aligns the continuation bar under this row's branch character.
            child_prefix = prefix + ("  " if last else "│ ")
            if level + 1 < depth:
                _walk(span_id, level + 1, child_prefix)
            elif hidden:
                # Say what was elided rather than let the tree end in a lie.
                lines.append(f"{child_prefix}└─ … {hidden} more span(s) — see the waterfall")

    _walk(str(root.get("span_id") or ""), 0, " ")
    return "\n".join(lines)
