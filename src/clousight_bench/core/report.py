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

from clousight_bench.core.campaign import CAMPAIGNS_DIRNAME
from clousight_bench.core.fingerprints import record_digest
from clousight_bench.core.record import SCHEMA_VERSION, RecordError, ResultRecord
from clousight_bench.core.runplan import AGGREGATES_DIRNAME
from clousight_bench.core.statistics import NumericKind, aggregate_measurements
from clousight_bench.core.store import validate_sidecar

_SKIP_FILES = {"comparison.json", "migration-manifest.json", "publish-receipts.jsonl"}
_STATUS_MARK = {
    "completed": "✅",
    "unsupported": "➖",
    "failed": "❌",
    "invalid": "⚠️",
}

# Capability measurement -> matrix row label. Drives the platform x capability
# matrix ("who supports what at a glance"). Absent = never probed (shown "·").
_CAPABILITY_MEASUREMENTS: dict[str, str] = {
    "state_capability": "state-persistence",
    "trace_capability": "trace",
    "otel_export_supported": "otel-export",
    "scaling_capability": "elasticity",
    "mcp": "tool:mcp",
    "openapi": "tool:openapi",
    "native": "tool:native",
}


def _capability_mark(value: Any) -> str:
    if value in (True, "supported"):
        return "✅"
    if value in (False, "unsupported"):
        return "✗"
    return "?"


def _capability_matrix(records: dict[tuple[str, str, str], ResultRecord]) -> list[str]:
    """A capability x platform grid from the latest records. Presence/absence only,
    deliberately NOT a score."""
    grid: dict[str, dict[str, str]] = defaultdict(dict)
    platforms: set[str] = set()
    for (_domain, _task, platform), rec in records.items():
        if rec.status not in ("completed", "unsupported"):
            continue
        for key, label in _CAPABILITY_MEASUREMENTS.items():
            m = rec.measurements.get(key)
            if isinstance(m, dict) and "value" in m:
                platforms.add(platform)
                grid[label][platform] = _capability_mark(m["value"])
    if not grid:
        return []
    cols = sorted(platforms)
    lines = [
        "## Capability matrix",
        "",
        "Presence/absence only (✅ supported · ✗ absent · · not probed). Not a score.",
        "",
    ]
    lines.append("| capability | " + " | ".join(cols) + " |")
    lines.append("|---" * (len(cols) + 1) + "|")
    for label in sorted(grid):
        cells = " | ".join(grid[label].get(p, "·") for p in cols)
        lines.append(f"| {label} | {cells} |")
    lines.append("")
    return lines


MAX_SERIES_POINTS = 500


def _load_series(results_dir: Path) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Read every ``*.series.parquet`` (flat fetch layout) and ``**/series.parquet``
    (nested run layout) into ``{task_id: {series: [{"t","value","unit"}]}}``.

    Series are tiny here; above ``MAX_SERIES_POINTS`` a stride-downsample is applied
    and the truncation is logged (never silent)."""
    import pyarrow.parquet as pq

    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    seen: set[Path] = set()
    for pattern in ("*.series.parquet", "**/series.parquet"):
        for path in sorted(Path(results_dir).glob(pattern)):
            rp = path.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            try:
                tbl = pq.read_table(path, columns=["task_id", "series", "t", "value", "unit"])
            except (OSError, ValueError, KeyError):
                continue
            for row in tbl.to_pylist():
                tid, sname = str(row["task_id"]), str(row["series"])
                out.setdefault(tid, {}).setdefault(sname, []).append(
                    {"t": row["t"], "value": row["value"], "unit": row.get("unit") or ""}
                )
    for tid, byname in out.items():
        for sname, pts in byname.items():
            pts.sort(key=lambda p: p["t"])
            if len(pts) > MAX_SERIES_POINTS:
                stride = len(pts) // MAX_SERIES_POINTS + 1
                byname[sname] = pts[::stride]
                print(
                    f"clousight-bench: downsampled {tid}/{sname} {len(pts)}->{len(byname[sname])} points",
                    file=sys.stderr,
                )
    return out


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
        # Run-plan aggregates are summaries of records, not records; the plan
        # writer owns them and they never parse as a 0.2 ResultRecord.
        if AGGREGATES_DIRNAME in path.relative_to(results_dir).parts:
            continue
        # Campaign manifests are live progress state, not records; skip wholesale.
        if CAMPAIGNS_DIRNAME in path.relative_to(results_dir).parts:
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
                    expected_digest = data.get("fingerprints", {}).get("record_digest")
                    if not isinstance(expected_digest, str) or record_digest(data) != expected_digest:
                        reason = "record digest mismatch"
                    else:
                        _, sidecar_error = validate_sidecar(results_dir, data)
                        if sidecar_error is not None:
                            reason = sidecar_error
                        else:
                            records.append(ResultRecord.from_dict(data))
                except (KeyError, TypeError, ValueError, RecordError) as exc:
                    reason = f"malformed record: {type(exc).__name__}: {exc}"
        if reason is not None:
            skipped.append(f"{path}: {reason}")

    for line in skipped:
        print(f"clousight-bench: skipped {line}", file=sys.stderr)
    if skipped:
        print(
            f"clousight-bench: skipped {len(skipped)} result file(s), read {len(records)}",
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


def _fmt_cost(rec: ResultRecord) -> str:
    """Cost from the pricing enricher's ``extensions["pricing"]``, or ``—``.

    Shows the reference cost only for records the enricher actually priced (those
    with usage measurements); everything else stays ``—``. A ``(partial)`` marker
    flags a cost computed while some usage went unpriced — the uncovered units are
    named in the red-flags section. This is a per-cell figure, never summed across
    dimensions (see the no-blended-score contract)."""
    pricing = rec.extensions.get("pricing")
    if not isinstance(pricing, dict) or "cost_usd" not in pricing:
        return "—"
    currency = pricing.get("currency", "USD")
    partial = " (partial)" if pricing.get("uncovered") else ""
    net = pricing["cost_usd"]
    list_cost = pricing.get("list_cost_usd")
    discount = pricing.get("discount_usd") or 0
    if list_cost is not None and discount:
        return f"${net:.6g} {currency} (list ${list_cost:.6g}, −${discount:.6g}){partial}"
    return f"${net:.6g} {currency}{partial}"


def _is_warmup(rec: ResultRecord) -> bool:
    """A warmup repeat is thrown-away evidence: never a representative or a stat."""
    core = rec.extensions.get("core", {})
    plan = core.get("run_plan", {}) if isinstance(core, dict) else {}
    return isinstance(plan, dict) and plan.get("role") == "warmup"


def _fmt_number(value: Any) -> str:
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.4g}"
    return str(value)


def _fmt_stat(name: str, summary: dict[str, Any]) -> str:
    evidence = f" [{summary['evidence']}]" if summary.get("evidence") else ""
    if summary.get("kind") == NumericKind:
        unit = f" {summary['unit']}" if summary.get("unit") else ""
        mean = _fmt_number(summary["mean"])
        stdev = _fmt_number(summary["stdev"])
        p95 = _fmt_number(summary["p95"])
        body = f"{mean}±{stdev}{unit} (n={summary['n']}, p95={p95})"
    else:
        mode = summary.get("mode")
        agreement = summary.get("agreement", 0.0)
        body = f"{mode} ×{int(round(agreement * summary['n']))}/{summary['n']}"
        if summary.get("distinct", 1) > 1:
            body += " (disagreement)"
    return f"{name}={body}{evidence}"


def _stats_section(measured: list[ResultRecord]) -> list[str]:
    """Aggregate every comparable group of repeats into one distribution each.

    A group is one (domain, task, adapter, benchmark, environment) fingerprint
    tuple, so two runs are pooled only when they are literally the same
    benchmark in the same environment. Groups with a single run add nothing the
    matrix does not already show, so only real repeats (n >= 2) are rendered.
    """
    groups: dict[tuple[str, str, str, str, str], list[ResultRecord]] = defaultdict(list)
    for rec in measured:
        if rec.status not in ("completed", "unsupported"):
            continue
        key = (
            rec.identity.domain,
            rec.identity.task_id,
            rec.identity.adapter,
            rec.fingerprints.benchmark,
            rec.fingerprints.environment,
        )
        groups[key].append(rec)

    rendered: list[str] = []
    for key in sorted(groups):
        recs = groups[key]
        if len(recs) < 2:
            continue
        domain, task, adapter, benchmark, _env = key
        aggregates = aggregate_measurements([r.measurements for r in recs])
        if not aggregates:
            continue
        short = benchmark.removeprefix("sha256:")[:12]
        rendered.append(f"- `{domain}/{task}` on **{adapter}** (n={len(recs)}, benchmark `{short}`)")
        for name, summary in sorted(aggregates.items()):
            rendered.append(f"  - {_fmt_stat(name, summary)}")

    if not rendered:
        return []
    return ["## Repeated-run statistics", "", *rendered, ""]


def _comparability_flags(measured: list[ResultRecord]) -> list[str]:
    """Warn when one cell mixes benchmarks or code versions that cannot be compared."""
    cells: dict[tuple[str, str, str], list[ResultRecord]] = defaultdict(list)
    for rec in measured:
        cells[(rec.identity.domain, rec.identity.task_id, rec.identity.adapter)].append(rec)

    flags: list[str] = []
    for (domain, task, adapter), recs in sorted(cells.items()):
        where = f"- `{domain}/{task}` on **{adapter}**"
        benchmarks = {r.fingerprints.benchmark for r in recs}
        if len(benchmarks) > 1:
            flags.append(
                f"{where}: {len(benchmarks)} distinct benchmark fingerprints — these "
                "are different benchmarks and must not be compared as one"
            )
            continue
        implementations = {r.fingerprints.implementation for r in recs}
        if len(implementations) > 1:
            flags.append(
                f"{where}: same benchmark, {len(implementations)} distinct "
                "implementation fingerprints — the code changed, so compare only "
                "with that caveat"
            )
    return flags


def _usd(amount: float) -> str:
    return f"${amount:.6g} USD"


def _cost_summary(records: list[ResultRecord]) -> list[str]:
    """Total modeled cost across every priced run, with itemized detail.

    Summing money across runs is meaningful (unlike blending performance
    dimensions): this answers "what did this campaign cost". It counts EVERY
    execution that carried a pricing extension -- including warmups and every
    repeat, since each one actually spent -- so it reflects real spend, not the
    representative per-cell figure in the matrix. It is a modeled reference cost,
    never a vendor bill."""
    priced = [
        r
        for r in records
        if isinstance(r.extensions.get("pricing"), dict) and "cost_usd" in r.extensions["pricing"]
    ]
    if not priced:
        return []

    total = 0.0
    adapter_runs: dict[str, int] = defaultdict(int)
    adapter_cost: dict[str, float] = defaultdict(float)
    unit_qty: dict[str, float] = defaultdict(float)
    unit_cost: dict[str, float] = defaultdict(float)
    uncovered: set[str] = set()
    partial_runs = 0
    for r in priced:
        pricing = r.extensions["pricing"]
        total += float(pricing.get("cost_usd") or 0.0)
        adapter_runs[r.identity.adapter] += 1
        adapter_cost[r.identity.adapter] += float(pricing.get("cost_usd") or 0.0)
        for item in pricing.get("breakdown", []):
            if isinstance(item, dict):
                unit = str(item.get("unit"))
                unit_qty[unit] += float(item.get("qty") or 0.0)
                unit_cost[unit] += float(item.get("subtotal") or 0.0)
        if pricing.get("uncovered"):
            partial_runs += 1
            uncovered.update(str(u) for u in pricing["uncovered"])

    note = f" ({partial_runs} had unpriced usage)" if partial_runs else ""
    lines = ["## Cost summary", ""]
    lines.append(
        f"Modeled reference cost across **{len(priced)}** run(s), including warmups "
        f"and repeats — total spend, not the per-cell latest above. Not a vendor "
        f"bill.{note}"
    )
    lines.append("")
    lines.append(f"- **Total: {_usd(round(total, 6))}**")
    lines.append("")
    lines.append("| adapter | runs | cost |")
    lines.append("|---|---|---|")
    for adapter in sorted(adapter_cost):
        lines.append(f"| {adapter} | {adapter_runs[adapter]} | {_usd(round(adapter_cost[adapter], 6))} |")
    lines.append("")
    if unit_cost:
        lines.append("| usage unit | qty | cost |")
        lines.append("|---|---|---|")
        for unit in sorted(unit_cost):
            lines.append(f"| {unit} | {_fmt_number(unit_qty[unit])} | {_usd(round(unit_cost[unit], 6))} |")
        lines.append("")
    if uncovered:
        lines.append(f"Unpriced usage seen (excluded from cost): {', '.join(sorted(uncovered))}")
        lines.append("")
    return lines


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
                f"{where}: PERSIST is `{persist_state or 'absent'}` — result storage is not trustworthy"
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
        pricing = rec.extensions.get("pricing")
        if isinstance(pricing, dict) and pricing.get("uncovered"):
            uncovered = ", ".join(str(u) for u in pricing["uncovered"])
            flags.append(
                f"{where}: cost is partial — no price for {uncovered}; the reported cost excludes these units"
            )
    return flags


def generate_report(results_dir: Path, out_path: Path | None = None) -> str:
    results_dir = Path(results_dir)
    out_path = out_path or (results_dir / "comparison.md")

    records = _load_results(results_dir)
    if not records:
        report = f"# Clousight Bench comparison\n\nNo schema 0.2 results found under `{results_dir}`.\n"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        return report

    # Warmup repeats are throw-away evidence: never a matrix representative, a
    # statistic or a comparability signal.
    measured = [rec for rec in records if not _is_warmup(rec)]
    latest = _latest_per_cell(measured)
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
        lines.append("| adapter | status | measurements | cost | benchmark fingerprint | core |")
        lines.append("|---|---|---|---|---|---|")
        for adapter, rec in sorted(adapters.items()):
            mark = _STATUS_MARK.get(rec.status, rec.status)
            short = rec.fingerprints.benchmark.removeprefix("sha256:")[:12]
            exec_mark = ""
            if rec.environment.execution != "unknown":
                exec_mark = f" ({rec.environment.execution})"
            lines.append(
                f"| {adapter}{exec_mark} | {mark} {rec.status} | "
                f"{_fmt_measurements(rec.measurements)} | {_fmt_cost(rec)} | `{short}` | "
                f"{rec.identity.core_version} |"
            )
        lines.append("")

    lines.extend(_capability_matrix(latest))

    # Cost summary reads ALL records (every execution spent), not just the latest
    # per cell -- this is total campaign spend with itemized detail.
    lines.extend(_cost_summary(records))

    lines.extend(_stats_section(measured))

    comparability = _comparability_flags(measured)
    if comparability:
        lines.append("## Comparability")
        lines.append("")
        lines.extend(comparability)
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
