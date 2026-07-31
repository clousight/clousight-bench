"""Pure-Python inline SVG charts (no JS, no external refs). Small on purpose."""
from __future__ import annotations

from clousight_bench.core.reporting.bundle import ChartSpec

_W, _H, _PAD = 520, 240, 30
_COLORS = ["#1E3A8A", "#10B981", "#F59E0B", "#3B82F6", "#EF4444"]


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt(v: float) -> str:
    return f"{v:.6g}"


def grouped_bar_svg(chart: ChartSpec, labels: list[str]) -> str:
    series = chart.series
    n_groups = max(len(labels), 1)
    n_series = len(series) or 1
    all_vals = [p for s in series for p in s["points"]] or [1.0]
    vmax = max(all_vals) or 1.0
    gw = (_W - 2 * _PAD) / n_groups
    bw = gw / (n_series + 1)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_W} {_H}" '
             f'width="{_W}" height="{_H}" role="img">',
             f'<line x1="{_PAD}" y1="{_H-_PAD}" x2="{_W-_PAD}" y2="{_H-_PAD}" stroke="#888"/>']
    for gi, label in enumerate(labels):
        gx = _PAD + gi * gw
        for si, s in enumerate(series):
            val = s["points"][gi] if gi < len(s["points"]) else 0.0
            h = (val / vmax) * (_H - 2 * _PAD)
            x = gx + (si + 0.5) * bw
            y = _H - _PAD - h
            color = _COLORS[si % len(_COLORS)]
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw*0.8:.1f}" '
                         f'height="{h:.1f}" fill="{color}"><title>{_esc(s["name"])}: '
                         f'{_fmt(val)}</title></rect>')
            parts.append(f'<text x="{x:.1f}" y="{y-2:.1f}" font-size="8">{_fmt(val)}</text>')
        parts.append(f'<text x="{gx+gw/2:.1f}" y="{_H-_PAD+12:.1f}" font-size="9" '
                     f'text-anchor="middle">{_esc(label)}</text>')
    for si, s in enumerate(series):
        ly = _PAD + si * 14
        parts.append(f'<rect x="{_W-_PAD-90}" y="{ly}" width="10" height="10" '
                     f'fill="{_COLORS[si % len(_COLORS)]}"/>')
        parts.append(f'<text x="{_W-_PAD-76}" y="{ly+9}" font-size="9">{_esc(s["name"])}</text>')
    parts.append(f'<text x="4" y="12" font-size="9">{_esc(chart.y_label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def bar_svg(chart: ChartSpec, labels: list[str]) -> str:
    flat = ChartSpec(chart.kind, chart.x_label, chart.y_label,
                     [{"name": s["name"], "points": [s["points"][0] if s["points"] else 0.0]}
                      for s in chart.series])
    return grouped_bar_svg(flat, [chart.x_label])


def line_svg(chart: ChartSpec) -> str:
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_W} {_H}" '
             f'width="{_W}" height="{_H}" role="img">']
    all_y = [p[1] for s in chart.series for p in s["points"]] or [1.0]
    all_x = [p[0] for s in chart.series for p in s["points"]] or [1.0]
    ymax, xmax = max(all_y) or 1.0, max(all_x) or 1.0
    for si, s in enumerate(chart.series):
        pts = " ".join(
            f'{_PAD + (p[0]/xmax)*(_W-2*_PAD):.1f},{_H-_PAD-(p[1]/ymax)*(_H-2*_PAD):.1f}'
            for p in s["points"])
        parts.append(f'<polyline fill="none" stroke="{_COLORS[si % len(_COLORS)]}" '
                     f'points="{pts}"><title>{_esc(s["name"])}</title></polyline>')
    parts.append("</svg>")
    return "".join(parts)
