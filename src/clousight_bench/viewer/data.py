"""Read-only readers over a results directory (stdlib only).

Record files live at ``results_dir/<domain>/<adapter>/<task_id>-<run_id>.json``
(see core/store.py:ResultStore._record_path). Everything here is strictly
read-only and tolerant: unparseable files are skipped with a warning, and every
artifact read is contained inside ``results_dir`` via resolve + is_relative_to.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Reserved top-level subtrees of results_dir that never contain record files.
_SKIP_DIRS = frozenset({"aggregates", "campaigns", "artifacts", "traces", "debug"})

#: run_ids are used to locate files on disk, so they must be plain tokens.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+\Z")


def _iter_record_files(results_dir: Path) -> list[Path]:
    """Record JSON files at ``<domain>/<adapter>/*.json``, skipping reserved subtrees."""
    files: list[Path] = []
    if not results_dir.is_dir():
        return files
    for domain_dir in sorted(results_dir.iterdir()):
        if not domain_dir.is_dir() or domain_dir.name in _SKIP_DIRS or domain_dir.name.startswith("."):
            continue
        for adapter_dir in sorted(domain_dir.iterdir()):
            if not adapter_dir.is_dir() or adapter_dir.name in _SKIP_DIRS or adapter_dir.name.startswith("."):
                continue
            for path in sorted(adapter_dir.glob("*.json")):
                if path.is_file() and not path.name.startswith("."):
                    files.append(path)
    return files


def _read_record(path: Path) -> dict[str, Any] | None:
    """Parse one record file; on any failure warn and return None (never raise)."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("viewer: skipping unreadable record %s: %s", path, exc)
        return None
    if not isinstance(loaded, dict):
        logger.warning("viewer: skipping non-object record %s", path)
        return None
    return loaded


def _summarize(record: dict[str, Any]) -> dict[str, Any]:
    """Flatten a full record dict into the list_records summary shape."""
    run = record.get("run") or {}
    identity = record.get("identity") or {}
    provenance = record.get("provenance") or {}
    measurements_raw = record.get("measurements") or {}
    measurements = {
        key: entry.get("value") for key, entry in measurements_raw.items() if isinstance(entry, dict)
    }
    artifacts = record.get("artifacts") or []
    has_trajectory = any(isinstance(a, dict) and a.get("kind") == "trajectory" for a in artifacts)
    return {
        "run_id": run.get("run_id", ""),
        "domain": identity.get("domain", ""),
        "task_id": identity.get("task_id", ""),
        "adapter": identity.get("adapter", ""),
        "status": record.get("status", ""),
        "started_at": run.get("started_at", ""),
        "suite_id": provenance.get("suite_id", ""),
        "scaffold": provenance.get("scaffold", ""),
        "measurements": measurements,
        "has_trajectory": has_trajectory,
    }


# Per-file summary cache: path -> (mtime_ns, size, summary | None).  A record
# file is immutable once written (atomic rename), so (mtime, size) identity is
# sufficient; changed/new files re-parse, deleted files fall out naturally.
_SUMMARY_CACHE: dict[Path, tuple[int, int, dict[str, Any] | None]] = {}


def _cached_summary(path: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (stat.st_mtime_ns, stat.st_size)
    hit = _SUMMARY_CACHE.get(path)
    if hit is not None and (hit[0], hit[1]) == key:
        return hit[2]
    record = _read_record(path)
    summary = _summarize(record) if record is not None else None
    _SUMMARY_CACHE[path] = (key[0], key[1], summary)
    return summary


def list_records(results_dir: Path) -> list[dict[str, Any]]:
    """Summaries of every record under ``results_dir``, newest started_at first.

    Unchanged files are served from an (mtime, size)-keyed cache, so a large
    results dir costs one stat per file per request, not one parse.
    """
    live_paths = _iter_record_files(results_dir)
    summaries = [s for path in live_paths for s in (_cached_summary(path),) if s is not None]
    # Drop cache entries for files that no longer exist (bounded by live set).
    stale = set(_SUMMARY_CACHE) - set(live_paths)
    for path in stale:
        del _SUMMARY_CACHE[path]
    summaries.sort(key=lambda s: str(s.get("started_at", "")), reverse=True)
    return summaries


def count_records(results_dir: Path) -> int:
    """The number of record files — a stat-level walk, no JSON parsing."""
    return len(_iter_record_files(results_dir))


def load_record(results_dir: Path, run_id: str) -> dict[str, Any] | None:
    """The full on-disk record dict for ``run_id``, or None if absent/invalid."""
    if not _RUN_ID_RE.match(run_id):
        return None
    suffix = f"-{run_id}.json"
    for path in _iter_record_files(results_dir):
        if not path.name.endswith(suffix):
            continue
        record = _read_record(path)
        if record is not None and (record.get("run") or {}).get("run_id") == run_id:
            return record
    return None


def load_trajectory(results_dir: Path, run_id: str) -> dict[str, Any] | None:
    """Parsed trajectory spans for ``run_id``: ``{"spans": [...], "t0": float}``.

    Returns None when the record, its trajectory artifact, or the artifact file
    is missing — or when the artifact path escapes ``results_dir``.
    """
    record = load_record(results_dir, run_id)
    if record is None:
        return None
    artifact = next(
        (a for a in record.get("artifacts") or [] if isinstance(a, dict) and a.get("kind") == "trajectory"),
        None,
    )
    if artifact is None or not isinstance(artifact.get("path"), str):
        return None

    root = results_dir.resolve()
    candidate = (results_dir / "artifacts" / artifact["path"]).resolve()
    if not candidate.is_relative_to(root):
        logger.warning(
            "viewer: run %s trajectory path %r escapes results_dir; refusing to read",
            run_id,
            artifact["path"],
        )
        return None
    if not candidate.is_file():
        return None

    spans: list[dict[str, Any]] = []
    try:
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("viewer: run %s: cannot read trajectory %s: %s", run_id, candidate, exc)
        return None
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            span = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.warning("viewer: run %s: skipping bad span line %d: %s", run_id, lineno, exc)
            continue
        if isinstance(span, dict):
            spans.append(_render_span(span))
        else:
            logger.warning(
                "viewer: run %s trajectory line %d is valid JSON but not an object; skipping",
                run_id,
                lineno,
            )

    t_starts = [s["t_start"] for s in spans if isinstance(s.get("t_start"), (int, float))]
    t0 = float(min(t_starts)) if t_starts else 0.0
    return {"spans": spans, "t0": t0}


def _v3_kind(attributes: dict[str, Any]) -> str:
    """Derive the render kind from a v3 span's semconv discriminator attributes."""
    op = attributes.get("gen_ai.operation.name")
    if op == "execute_tool":
        return "tool_call"
    if op is not None or any(k.startswith("gen_ai.") for k in attributes):
        return "llm_call"
    if any(k.startswith("db.") for k in attributes):
        return "query"
    phase = attributes.get("csbench.phase")
    if isinstance(phase, str) and phase:
        return "phase"
    return "span"


def _render_span(span: dict[str, Any]) -> dict[str, Any]:
    """Normalize a trajectory span to the waterfall's render shape.

    Legacy v2 spans already carry it (``kind``/``t_start`` seconds/``attrs``);
    v3 (OTel-native) spans are projected: ns -> seconds, semconv attributes ->
    ``kind``, ``attributes`` -> ``attrs``, OTel status names -> lowercase.
    """
    if "attributes" not in span and "start_unix_nano" not in span:
        return span  # legacy v2 — already the render shape
    raw_attributes = span.get("attributes")
    attributes: dict[str, Any] = raw_attributes if isinstance(raw_attributes, dict) else {}
    start_ns = span.get("start_unix_nano")
    end_ns = span.get("end_unix_nano")
    return {
        "span_id": span.get("span_id", ""),
        "trace_id": span.get("trace_id", ""),
        "parent_id": span.get("parent_span_id") or None,
        "name": span.get("name", ""),
        "kind": _v3_kind(attributes),
        "t_start": (start_ns / 1e9) if isinstance(start_ns, (int, float)) else 0.0,
        "t_end": (end_ns / 1e9) if isinstance(end_ns, (int, float)) else 0.0,
        "status": "error" if span.get("status") == "ERROR" else "ok",
        "attrs": dict(attributes),
    }
