"""ReportBundle: the vendor-agnostic 'what to show' model (the report engine).

Turns records into a JSON-serializable bundle of panels + chart specs. Renderer-
agnostic (carries chart DATA, never SVG). Execution-isolated: simulated and live
never share a comparison.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from clousight_bench.core.report import _CAPABILITY_MEASUREMENTS, _capability_mark
from clousight_bench.core.schema import ResultRecord

BUNDLE_SCHEMA = "report-bundle/1.0"


@dataclass
class ChartSpec:
    kind: str
    x_label: str
    y_label: str
    series: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "x_label": self.x_label,
                "y_label": self.y_label, "series": list(self.series)}


@dataclass
class Cell:
    platform: str
    status: str
    execution: str
    metrics: list[dict[str, Any]]
    agg_stats: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {"platform": self.platform, "status": self.status,
             "execution": self.execution, "metrics": list(self.metrics)}
        if self.agg_stats is not None:
            d["agg_stats"] = self.agg_stats
        return d


@dataclass
class Panel:
    key: str
    title: str
    evidence: str
    task_ids: list[str]
    cells: list[Cell]
    chart: ChartSpec | None = None
    comparison: bool = False
    tab: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "title": self.title, "evidence": self.evidence,
                "task_ids": list(self.task_ids), "cells": [c.to_dict() for c in self.cells],
                "chart": self.chart.to_dict() if self.chart else None,
                "comparison": self.comparison, "tab": self.tab}


@dataclass
class DomainReport:
    domain: str
    profile: str
    platforms: list[str]
    capability_matrix: dict[str, dict[str, str]]
    panels: list[Panel]
    red_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"domain": self.domain, "profile": self.profile,
                "platforms": list(self.platforms),
                "capability_matrix": self.capability_matrix,
                "panels": [p.to_dict() for p in self.panels],
                "red_flags": list(self.red_flags)}


@dataclass
class ReportBundle:
    schema: str
    results_dir: str
    generated_at: str
    domains: list[DomainReport]

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "results_dir": self.results_dir,
                "generated_at": self.generated_at,
                "domains": [d.to_dict() for d in self.domains]}


def _metric(rec: ResultRecord, name: str) -> dict[str, Any] | None:
    m = rec.measurements.get(name)
    if not isinstance(m, dict):
        return None
    value = m.get("value")
    unit, agg = m.get("unit", ""), m.get("aggregation", "")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"name": name, "value_num": float(value), "value_str": None,
                "unit": unit, "aggregation": agg}
    return {"name": name, "value_num": None, "value_str": str(value),
            "unit": unit, "aggregation": agg}


def _capability_matrix(latest: dict) -> dict[str, dict[str, str]]:
    grid: dict[str, dict[str, str]] = defaultdict(dict)
    for (_task, platform, _exec), rec in latest.items():
        if rec.status not in ("completed", "unsupported"):
            continue
        for mkey, label in _CAPABILITY_MEASUREMENTS.items():
            m = rec.measurements.get(mkey)
            if isinstance(m, dict) and "value" in m:
                grid[label][platform] = _capability_mark(m["value"])
    return {k: dict(v) for k, v in grid.items()}


def _agg_cell(agg: dict[str, Any], metric_keys: list[str]) -> Cell:
    """Build an aggregate Cell from a RunPlanAggregate dict."""
    measurements = agg.get("measurements", {})
    platform = agg.get("identity", {}).get("adapter", "")
    n = agg.get("plan", {}).get("repeat", 0)
    per_metric: dict[str, Any] = {
        name: stats for name, stats in measurements.items()
        if isinstance(stats, dict)
    }
    metrics: list[dict[str, Any]] = []
    for name in metric_keys:
        stats = measurements.get(name)
        if not isinstance(stats, dict):
            continue
        if stats.get("kind") == "numeric":
            metrics.append({"name": name, "value_num": stats.get("mean"),
                             "value_str": None, "unit": "", "aggregation": "mean"})
        else:
            mode = stats.get("mode")
            metrics.append({"name": name, "value_num": None,
                             "value_str": str(mode) if mode is not None else None,
                             "unit": "", "aggregation": "mode"})
    agg_stats = {
        "n": n,
        "plan_id": agg.get("plan_id", ""),
        "comparable": agg.get("comparable", True),
        "warnings": list(agg.get("notes", [])),
        "per_metric": per_metric,
    }
    return Cell(platform=platform, status="aggregate", execution="",
                metrics=metrics, agg_stats=agg_stats)


def _build_agg_cells(domain: str, panel: Panel,
                     agg_lookup: dict[tuple[str, str, str], dict[str, Any]]) -> list[Cell]:
    """Append one aggregate Cell per platform that has aggregate data for this panel."""
    # Collect metric_keys from existing individual cells (preserves display order)
    metric_keys: list[str] = []
    for c in panel.cells:
        if c.agg_stats is not None:
            continue
        for m in c.metrics:
            if m["name"] not in metric_keys:
                metric_keys.append(m["name"])

    platforms = sorted({c.platform for c in panel.cells if c.agg_stats is None})
    result: list[Cell] = []
    for platform in platforms:
        merged_measurements: dict[str, Any] = {}
        best_n = 0
        merged_plan_id = ""
        merged_comparable = True
        merged_warnings: list[str] = []
        found = False
        for task_id in panel.task_ids:
            agg = agg_lookup.get((domain, task_id, platform))
            if agg is None:
                continue
            found = True
            n = agg.get("plan", {}).get("repeat", 0)
            plan_id = agg.get("plan_id", "")
            if n > best_n or (n == best_n and plan_id > merged_plan_id):
                best_n, merged_plan_id = n, plan_id
            merged_measurements.update(agg.get("measurements", {}))
            if not agg.get("comparable", True):
                merged_comparable = False
                merged_warnings.extend(agg.get("notes", []))
        if found:
            merged_agg: dict[str, Any] = {
                "identity": {"adapter": platform},
                "plan": {"repeat": best_n},
                "plan_id": merged_plan_id,
                "comparable": merged_comparable,
                "notes": merged_warnings,
                "measurements": merged_measurements,
            }
            result.append(_agg_cell(merged_agg, metric_keys))
    return result


def build_bundle(records, *, results_dir: str, generated_at: str, profiles,
                 aggregates=None) -> ReportBundle:
    from clousight_bench.core.report import _is_warmup

    # Build aggregate lookup: {(domain, task_id, platform): agg_dict}
    # Deduplicate: highest n wins; tie-break by plan_id lexicographic descending.
    agg_lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for agg in (aggregates or []):
        identity = agg.get("identity", {})
        key = (identity.get("domain", ""), identity.get("task_id", ""),
               identity.get("adapter", ""))
        existing = agg_lookup.get(key)
        this_n = agg.get("plan", {}).get("repeat", 0)
        ex_n = existing.get("plan", {}).get("repeat", 0) if existing else -1
        if existing is None or this_n > ex_n or (
            this_n == ex_n and agg.get("plan_id", "") > existing.get("plan_id", "")
        ):
            agg_lookup[key] = agg

    measured = [r for r in records if not _is_warmup(r)]
    by_domain: dict[str, list[ResultRecord]] = defaultdict(list)
    for r in measured:
        by_domain[r.identity.domain].append(r)

    domains: list[DomainReport] = []
    for domain, recs in sorted(by_domain.items()):
        profile = profiles.get(domain) or profiles["__generic__"]
        latest: dict[tuple[str, str, str], ResultRecord] = {}
        for r in recs:
            latest[(r.identity.task_id, r.identity.adapter, r.environment.execution)] = r
        platforms = sorted({r.identity.adapter for r in recs})
        executions = {r.environment.execution for r in recs}
        red_flags: list[str] = []
        if "simulated" in executions and "live" in executions:
            red_flags.append(
                "This domain mixes simulated and live data — they are shown "
                "separately and must not be compared.")
        cap = _capability_matrix(latest)
        panels = profile.build_panels(latest)
        if agg_lookup:
            for panel in panels:
                panel.cells.extend(_build_agg_cells(domain, panel, agg_lookup))
        domains.append(DomainReport(domain, profile.name, platforms, cap, panels, red_flags))
    return ReportBundle(BUNDLE_SCHEMA, results_dir, generated_at, domains)
