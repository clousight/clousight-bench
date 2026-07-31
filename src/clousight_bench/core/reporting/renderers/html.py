"""Default zero-dependency HTML renderer: self-contained document + inline SVG."""
from __future__ import annotations

from clousight_bench.core.reporting.bundle import Panel, ReportBundle
from clousight_bench.core.reporting.renderers import svg
from clousight_bench.core.reporting.renderers.base import ReportRenderer

_DEFAULT_CSS = """
body{font-family:system-ui,sans-serif;margin:2rem;color:#222}
h1,h2,h3{color:#1a3a5a}
.badge{display:inline-block;padding:.1rem .4rem;border-radius:.3rem;font-size:.8rem}
.sim{background:#f6c453;color:#000}.live{background:#4c9a6a;color:#fff}
.unknown{background:#ddd;color:#333}
table{border-collapse:collapse;margin:.5rem 0}td,th{border:1px solid #ccc;padding:.2rem .5rem}
.flag{background:#fde8e8;border:1px solid #e0a0a0;padding:.5rem;border-radius:.3rem}
"""


def _esc(s: object) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _badge(execution: str) -> str:
    cls = {"simulated": "sim", "live": "live"}.get(execution, "unknown")
    return f'<span class="badge {cls}">{_esc(execution)}</span>'


def _panel_html(panel: Panel) -> str:
    metric_names: list[str] = []
    for c in panel.cells:
        for m in c.metrics:
            if m["name"] not in metric_names:
                metric_names.append(m["name"])
    head = "".join(f"<th>{_esc(n)}</th>" for n in metric_names)
    rows = []
    for c in panel.cells:
        vals = {m["name"]: (m["value_num"] if m["value_num"] is not None else m["value_str"])
                for m in c.metrics}
        cells = "".join(f"<td>{_esc(vals.get(n, ''))}</td>" for n in metric_names)
        rows.append(f"<tr><td>{_esc(c.platform)} {_badge(c.execution)}</td>{cells}</tr>")
    chart_html = ""
    if panel.chart:
        if panel.chart.kind == "grouped_bar":
            chart_html = svg.grouped_bar_svg(panel.chart, metric_names)
        elif panel.chart.kind == "bar":
            chart_html = svg.bar_svg(panel.chart, metric_names)
        elif panel.chart.kind == "line":
            chart_html = svg.line_svg(panel.chart)
    return (f"<h3>{_esc(panel.title)} <small>[{_esc(panel.evidence)}]</small></h3>"
            f"{chart_html}"
            f"<table><tr><th>platform</th>{head}</tr>{''.join(rows)}</table>")


class HtmlRenderer(ReportRenderer):
    name = "html"
    output_suffix = ".html"

    def render(self, bundle: ReportBundle, *, css: str = "") -> str:
        parts = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
                 "<title>Clousight Bench report</title>",
                 f"<style>{_DEFAULT_CSS}{css}</style></head><body>",
                 "<h1>Clousight Bench report</h1>",
                 f"<p>Data: locally collected · {_esc(bundle.results_dir)} · "
                 f"{_esc(bundle.generated_at)} · <code>{_esc(bundle.schema)}</code></p>"]
        for dom in bundle.domains:
            parts.append(f"<h2>{_esc(dom.domain)} <small>({_esc(dom.profile)})</small></h2>")
            for flag in dom.red_flags:
                parts.append(f'<p class="flag">{_esc(flag)}</p>')
            if dom.capability_matrix:
                cols = sorted({p for row in dom.capability_matrix.values() for p in row})
                header = "".join(f"<th>{_esc(p)}</th>" for p in cols)
                parts.append(f"<h3>Capability matrix</h3>"
                             f"<table><tr><th>capability</th>{header}</tr>")
                for cap, row in sorted(dom.capability_matrix.items()):
                    cells = "".join(f"<td>{_esc(row.get(p, '·'))}</td>" for p in cols)
                    parts.append(f"<tr><td>{_esc(cap)}</td>{cells}</tr>")
                parts.append("</table>")
            for panel in dom.panels:
                parts.append(_panel_html(panel))
        parts.append("</body></html>")
        return "".join(parts)
