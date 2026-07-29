"""Comparison report generator.

Reads every ResultRecord JSON under a results directory and renders a markdown
report: one comparison matrix per (domain, task) across platforms, plus a red-flag
list. Deliberately does NOT compute a blended cross-dimension score -- per-dimension
reporting only, because blended agent-benchmark rankings have near-zero agreement.

Every cell carries its evidence layer so a reader never confuses a controlled
measurement (C) with a documentation reading (A) or environment observation (B).
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from clousight_bench.core.schema import ResultRecord

# Metric keys that, at these values, are worth surfacing as a red flag.
_RED_FLAGS: dict[str, set[Any]] = {
    "recovery_mode": {"fail-fast", "no-fault-observed"},
    "final_state": {"aborted", "failed"},
    "job_succeeded": {False},
    "budgeted_success": {False},
}


def _load_results(results_dir: Path) -> list[ResultRecord]:
    records: list[ResultRecord] = []
    for path in sorted(results_dir.rglob("*.json")):
        if path.name == "comparison.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            records.append(ResultRecord.from_dict(data))
        except (json.JSONDecodeError, TypeError):
            continue
    return records


def _latest_per_cell(records: list[ResultRecord]) -> dict[tuple[str, str, str], ResultRecord]:
    """Keep the most recent record per (domain, task, platform)."""
    latest: dict[tuple[str, str, str], ResultRecord] = {}
    for rec in records:
        key = (rec.domain, rec.task_id, rec.platform)
        cur = latest.get(key)
        if cur is None or rec.started_at >= cur.started_at:
            latest[key] = rec
    return latest


def _fmt_metrics(metrics: dict[str, Any]) -> str:
    if not metrics:
        return "—"
    parts = [f"{k}={v}" for k, v in metrics.items()]
    return "<br>".join(parts)


def _red_flags(records: dict[tuple[str, str, str], ResultRecord]) -> list[str]:
    flags: list[str] = []
    for (domain, task, platform), rec in sorted(records.items()):
        if not rec.ok:
            why = rec.error or rec.notes or "see result"
            flags.append(f"- `{domain}/{task}` on **{platform}**: run not ok — {why}")
            continue
        for key, bad_values in _RED_FLAGS.items():
            if key in rec.metrics and rec.metrics[key] in bad_values:
                flags.append(f"- `{domain}/{task}` on **{platform}**: `{key}={rec.metrics[key]}`")
    return flags


def generate_report(results_dir: Path, out_path: Path | None = None) -> str:
    results_dir = Path(results_dir)
    out_path = out_path or (results_dir / "comparison.md")

    records = _load_results(results_dir)
    if not records:
        report = "# Clousight Bench comparison\n\nNo results found under " f"`{results_dir}`.\n"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        return report

    latest = _latest_per_cell(records)

    # group by (domain, task) -> {platform: record}
    by_task: dict[tuple[str, str], dict[str, ResultRecord]] = defaultdict(dict)
    for (domain, task, platform), rec in latest.items():
        by_task[(domain, task)][platform] = rec

    lines: list[str] = ["# Clousight Bench comparison", ""]
    lines.append("Per-dimension results only — no blended score. Evidence layers: "
                 "A=docs · B=observation · C=controlled measurement · D=marketing.")
    lines.append("")

    for (domain, task), platforms in sorted(by_task.items()):
        lines.append(f"## {domain} · {task}")
        lines.append("")
        lines.append("| platform | evidence | ok | metrics | config_hash | runner |")
        lines.append("|---|---|---|---|---|---|")
        for platform, rec in sorted(platforms.items()):
            lines.append(
                f"| {platform} | {rec.evidence_layer} | {'✅' if rec.ok else '❌'} | "
                f"{_fmt_metrics(rec.metrics)} | `{rec.config_hash}` | {rec.runner_version} |"
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
