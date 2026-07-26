"""Comparison report generator.

Reads every ResultRecord 0.2 under a results directory and renders a markdown
report: one comparison matrix per (domain, task) across adapters, plus a
red-flag list built from the records' own findings and statuses. Deliberately
does NOT compute a blended cross-dimension score -- per-dimension reporting
only, because blended agent-benchmark rankings have near-zero agreement.

Every measurement carries its own evidence layer, so a reader never confuses a
controlled measurement (C) with a documentation reading (A) or an environment
observation (B).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from clousight_bench.core.record import SCHEMA_VERSION, RecordError, ResultRecord

_SKIP_FILES = {"comparison.json", "migration-manifest.json", "publish-receipts.jsonl"}
_STATUS_MARK = {
    "completed": "✅",
    "unsupported": "➖",
    "failed": "❌",
    "invalid": "⚠️",
}


def _load_results(results_dir: Path) -> list[ResultRecord]:
    """Read every record we can, and say out loud which ones we could not.

    A silently skipped file is how a stale or half-migrated results directory
    turns into a confidently wrong report, so each skip is named on stderr with
    its reason -- and a pre-0.2 record gets the migration command with it.
    """
    records: list[ResultRecord] = []
    skipped: list[str] = []
    for path in sorted(results_dir.rglob("*.json")):
        if path.name in _SKIP_FILES:
            continue
        reason = None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            reason = f"unreadable: {exc.strerror or exc}"
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            reason = f"not valid JSON: {exc}"
        else:
            if not isinstance(data, dict):
                reason = f"not a JSON object (got {type(data).__name__})"
            elif str(data.get("schema_version", "")) != SCHEMA_VERSION:
                reason = (
                    f"schema {data.get('schema_version', '<missing>')!r}, "
                    f"expected {SCHEMA_VERSION!r} — run `csbench migrate-results` "
                    "to bring old runs forward"
                )
            else:
                try:
                    records.append(ResultRecord.from_dict(data))
                except (KeyError, TypeError, ValueError, RecordError) as exc:
                    reason = f"malformed record: {type(exc).__name__}: {exc}"
        if reason is not None:
            skipped.append(f"{path}: {reason}")

    for line in skipped:
        print(f"clousight-bench: skipped {line}", file=sys.stderr)
    if skipped:
        print(
            f"clousight-bench: skipped {len(skipped)} result file(s), "
            f"read {len(records)}",
            file=sys.stderr,
        )
    return records


def _latest_per_cell(
    records: list[ResultRecord],
) -> dict[tuple[str, str, str], ResultRecord]:
    """Keep the most recent record per (domain, task, adapter).

    Ties on ``started_at`` (same second, parallel runs) are broken by run_id, so
    two reports over the same directory never disagree.
    """
    latest: dict[tuple[str, str, str], ResultRecord] = {}
    for rec in records:
        key = (rec.identity.domain, rec.identity.task_id, rec.identity.adapter)
        current = latest.get(key)
        if current is None or (rec.run.started_at, rec.run.run_id) > (
            current.run.started_at,
            current.run.run_id,
        ):
            latest[key] = rec
    return latest


def _fmt_measurements(measurements: dict[str, Any]) -> str:
    if not measurements:
        return "—"
    parts = []
    for name, m in sorted(measurements.items()):
        if not isinstance(m, dict):
            parts.append(f"{name}={m}")
            continue
        unit = f" {m['unit']}" if m.get("unit") else ""
        evidence = f" [{m['evidence']}]" if m.get("evidence") else ""
        parts.append(f"{name}={m.get('value', '—')}{unit}{evidence}")
    return "<br>".join(parts)


def _red_flags(records: dict[tuple[str, str, str], ResultRecord]) -> list[str]:
    flags: list[str] = []
    for (domain, task, adapter), rec in sorted(records.items()):
        where = f"- `{domain}/{task}` on **{adapter}**"
        if rec.status != "completed":
            first = rec.errors[0] if rec.errors else None
            reason = (
                first.get("message") or first.get("code") or "see result"
                if isinstance(first, dict)
                else "see result"
            )
            flags.append(f"{where}: status `{rec.status}` — {reason}")
        persist_state = rec.run.stages.get("PERSIST")
        if persist_state != "ok":
            flags.append(
                f"{where}: PERSIST is `{persist_state or 'absent'}` — result storage "
                "is not trustworthy"
            )
        for error in rec.errors:
            if not isinstance(error, dict):
                flags.append(f"{where}: malformed recorded error — see result")
                continue
            stage = error.get("stage", "unknown")
            reason = error.get("message") or error.get("code") or "see result"
            flags.append(f"{where}: {stage} error — {reason}")
        for finding in rec.findings:
            if not isinstance(finding, dict):
                continue
            if finding.get("severity") in ("warning", "critical"):
                flags.append(
                    f"{where}: `{finding.get('code', 'unknown')}` "
                    f"({finding['severity']}) — {finding.get('summary', '')}"
                )
    return flags


def generate_report(results_dir: Path, out_path: Path | None = None) -> str:
    results_dir = Path(results_dir)
    out_path = out_path or (results_dir / "comparison.md")

    records = _load_results(results_dir)
    if not records:
        report = (
            "# Clousight Bench comparison\n\nNo schema 0.2 results found under "
            f"`{results_dir}`.\n"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        return report

    latest = _latest_per_cell(records)
    by_task: dict[tuple[str, str], dict[str, ResultRecord]] = defaultdict(dict)
    for (domain, task, adapter), rec in latest.items():
        by_task[(domain, task)][adapter] = rec

    lines: list[str] = ["# Clousight Bench comparison", ""]
    lines.append(
        "Per-dimension results only — no blended score. Evidence layers: "
        "A=docs · B=observation · C=controlled measurement · D=marketing."
    )
    lines.append("")

    for (domain, task), adapters in sorted(by_task.items()):
        lines.append(f"## {domain} · {task}")
        lines.append("")
        lines.append(
            "| adapter | status | measurements | benchmark fingerprint | core |"
        )
        lines.append("|---|---|---|---|---|")
        for adapter, rec in sorted(adapters.items()):
            mark = _STATUS_MARK.get(rec.status, rec.status)
            short = rec.fingerprints.benchmark.removeprefix("sha256:")[:12]
            lines.append(
                f"| {adapter} | {mark} {rec.status} | "
                f"{_fmt_measurements(rec.measurements)} | `{short}` | "
                f"{rec.identity.core_version} |"
            )
        lines.append("")

    flags = _red_flags(latest)
    lines.append("## Red flags")
    lines.append("")
    lines.extend(flags if flags else ["- none"])
    lines.append("")

    report = "\n".join(lines)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    return report
