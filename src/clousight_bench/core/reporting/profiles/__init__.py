"""Per-category report profiles: which panels/charts a domain shows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from clousight_bench.core.reporting.bundle import Cell, ChartSpec, Panel, _metric

# A panel spec: tab, key, title, task_ids, metric keys, chart kind ("" = table only).
_PanelSpec = tuple[str, str, str, list[str], list[str], str]

_AGENT_RUNTIME: list[_PanelSpec] = [
    ("Performance", "provisioning", "Provisioning lifecycle", ["T0.1", "T0.2"],
     ["provision_ready_ms", "teardown_ms", "residual_count"], "grouped_bar"),
    ("Performance", "latency", "Startup latency", ["T1.1"],
     ["cold_start_ms", "warm_start_p50_ms", "warm_start_p95_ms", "cold_warm_ratio"],
     "grouped_bar"),
    ("Performance", "sustained", "Sustained load & tail latency", ["T1.4"],
     ["throughput_rps", "p50_ms", "p99_ms", "jitter_ms", "error_rate_under_load"],
     "grouped_bar"),
    ("Performance", "warmpool", "Warm-pool retention", ["T1.5"],
     ["warm_retention_ms", "keeps_warm"], ""),
    ("Reliability", "state", "State persistence", ["T1.2"],
     ["state_persisted", "persistence_mode"], ""),
    ("Reliability", "recovery", "Fault recovery", ["T1.3"],
     ["recovery_mode", "total_attempts", "time_to_recovery_ms", "fault_hits",
      "budgeted_success"], ""),
    ("Reliability", "soak", "Soak availability", ["T1.6"],
     ["availability", "soak_error_rate", "soak_requests"], "bar"),
    ("Reliability", "ratelimit", "Rate limiting", ["T1.7"],
     ["throttle_onset_rps", "retry_after_ms", "honors_429"], ""),
    ("Reliability", "cancellation", "Timeout & cancellation", ["T1.8"],
     ["cancellation_honored", "teardown_on_cancel", "residual_on_cancel"], ""),
    ("Observability", "trace", "Tracing", ["T4.1", "T4.2"],
     ["span_completeness", "spans_present", "spans_expected", "otel_valid",
      "span_count"], "bar"),
    ("Cost", "cost", "Cost (list / discount / net)", ["T5.1"],
     ["invocations", "vcpu_hours", "list_cost_usd", "discount_usd", "cost_usd"],
     "grouped_bar"),
    ("Capability", "elasticity", "Elasticity", ["T5.2"],
     ["scales_cleanly", "concurrency_knee", "success_rate_at_peak", "p95_ms_at_peak"],
     "bar"),
    ("Capability", "tools", "Tool registration", ["T2.1"],
     ["mcp", "openapi", "native", "supported_count"], ""),
]

_COST_KEYS = ("list_cost_usd", "discount_usd", "cost_usd")


def _chart(kind: str, metric_keys: list[str], cells: list[Cell]) -> ChartSpec | None:
    if not kind:
        return None
    numeric = [k for k in metric_keys
               if any(m["name"] == k and m["value_num"] is not None
                      for c in cells for m in c.metrics)]
    if not numeric:
        return None
    series = []
    for c in cells:
        vals = {m["name"]: m["value_num"] for m in c.metrics if m["value_num"] is not None}
        series.append({"name": c.platform, "points": [vals.get(k, 0.0) for k in numeric]})
    return ChartSpec(kind=kind, x_label=" / ".join(numeric), y_label="value", series=series)


@dataclass
class Profile:
    name: str
    specs: list[_PanelSpec]
    cost_from_pricing: bool = False

    def build_panels(self, latest: dict) -> list[Panel]:
        panels: list[Panel] = []
        for tab, key, title, task_ids, metric_keys, chart_kind in self.specs:
            # One cell per (execution, platform): a group may span several tasks,
            # so merge each platform's metrics into a single comparison column
            # (metrics keep the spec order; first non-empty value per name wins).
            merged: dict[tuple[str, str], Cell] = {}
            for (task, platform, execu), rec in latest.items():
                if task not in task_ids:
                    continue
                metrics = self._metrics(rec, metric_keys)
                if not metrics:
                    continue
                cell = merged.get((execu, platform))
                if cell is None:
                    merged[(execu, platform)] = Cell(platform, rec.status, execu, list(metrics))
                    continue
                have = {m["name"] for m in cell.metrics}
                cell.metrics.extend(m for m in metrics if m["name"] not in have)
            by_exec: dict[str, list[Cell]] = {}
            for (execu, _platform), cell in merged.items():
                cell.metrics.sort(key=lambda m: metric_keys.index(m["name"]))
                by_exec.setdefault(execu, []).append(cell)
            for _execu, cells in by_exec.items():
                chart = _chart(chart_kind, metric_keys, cells)
                panels.append(Panel(key, title, "B", task_ids, cells, chart,
                                    comparison=len(cells) > 1, tab=tab))
        return panels

    def _metrics(self, rec, metric_keys) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        pricing = rec.extensions.get("pricing", {}) if self.cost_from_pricing else {}
        currency = pricing.get("currency", "USD") if isinstance(pricing, dict) else "USD"
        for name in metric_keys:
            if name in _COST_KEYS and isinstance(pricing, dict) and name in pricing:
                out.append({"name": name, "value_num": pricing[name], "value_str": None,
                            "unit": currency, "aggregation": ""})
                continue
            m = _metric(rec, name)
            if m is not None:
                out.append(m)
        return out


class _GenericProfile(Profile):
    def build_panels(self, latest: dict) -> list[Panel]:
        panels: list[Panel] = []
        by_task: dict[str, list[Cell]] = {}
        for (task, platform, execu), rec in latest.items():
            metrics = [_metric(rec, n) for n in rec.measurements]
            cells = [Cell(platform, rec.status, execu, [m for m in metrics if m])]
            by_task.setdefault(task, []).extend(cells)
        for task, cells in sorted(by_task.items()):
            panels.append(Panel(task, task, "C", [task], cells, None, len(cells) > 1))
        return panels


PROFILES: dict[str, Profile] = {
    "agent-runtime": Profile("agent-runtime", _AGENT_RUNTIME, cost_from_pricing=True),
    "__generic__": _GenericProfile("generic", []),
}
