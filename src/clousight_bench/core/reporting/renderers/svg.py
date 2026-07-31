"""Premium pure-Python inline SVG charts (no JS lib, no external refs).

Gridlines + y-axis ticks + rounded brand-gradient bars + a legend, with
``data-series``/``data-value``/``data-label`` hooks the inline interaction script
(charts_js) wires tooltips + legend toggles onto. Small on purpose.
"""
from __future__ import annotations

from clousight_bench.core.reporting.bundle import ChartSpec

_W, _H = 560, 260
_L, _R, _T, _B = 44, 16, 16, 34  # margins
_COLORS = ["hsl(217 71% 51%)", "hsl(213 73% 59%)", "hsl(219 52% 35%)",
           "hsl(38 92% 50%)", "hsl(174 62% 47%)"]
_GRAD = ("<defs><linearGradient id='barGrad' x1='0' y1='0' x2='0' y2='1'>"
         "<stop offset='0%' stop-color='hsl(217 71% 59%)'/>"
         "<stop offset='100%' stop-color='hsl(217 71% 45%)'/></linearGradient></defs>")


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt(v: float) -> str:
    return f"{v:.6g}"


def _open() -> list[str]:
    return [f"<svg class='chart' xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {_W} {_H}' "
            f"width='{_W}' height='{_H}' role='img'>{_GRAD}"]


def _grid(vmax: float) -> list[str]:
    parts = []
    plot_h = _H - _T - _B
    for i in range(5):
        y = _T + plot_h * i / 4
        val = vmax * (4 - i) / 4
        parts.append(f"<line class='grid' x1='{_L}' y1='{y:.1f}' x2='{_W-_R}' y2='{y:.1f}' "
                     f"stroke='hsl(214 20% 88%)' stroke-dasharray='2,3'/>")
        parts.append(f"<text x='{_L-4}' y='{y+3:.1f}' font-size='8' text-anchor='end' "
                     f"fill='hsl(222 12% 45%)'>{_fmt(val)}</text>")
    return parts


def _legend(series: list[dict]) -> list[str]:
    parts = []
    for si, s in enumerate(series):
        lx = _L + si * 96
        parts.append(f"<g class='legend-item' data-series='{_esc(s['name'])}'>"
                     f"<rect x='{lx}' y='{_H-12}' width='10' height='10' rx='2' "
                     f"fill='{_COLORS[si % len(_COLORS)]}'/>"
                     f"<text x='{lx+14}' y='{_H-3}' font-size='9' "
                     f"fill='hsl(222 20% 25%)'>{_esc(s['name'])}</text></g>")
    return parts


def grouped_bar_svg(chart: ChartSpec, labels: list[str]) -> str:
    series = chart.series
    n_groups, n_series = max(len(labels), 1), max(len(series), 1)
    vmax = max([p for s in series for p in s["points"]] or [1.0]) or 1.0
    plot_w, plot_h = _W - _L - _R, _H - _T - _B
    gw = plot_w / n_groups
    bw = gw / (n_series + 1)
    parts = _open() + _grid(vmax)
    for gi, label in enumerate(labels):
        gx = _L + gi * gw
        for si, s in enumerate(series):
            val = s["points"][gi] if gi < len(s["points"]) else 0.0
            h = (val / vmax) * plot_h
            x = gx + (si + 0.5) * bw
            y = _T + plot_h - h
            fill = "url(#barGrad)" if si == 0 else _COLORS[si % len(_COLORS)]
            parts.append(
                f"<rect x='{x:.1f}' y='{y:.1f}' width='{bw*0.8:.1f}' height='{max(h,0):.1f}' "
                f"rx='3' fill='{fill}' data-series='{_esc(s['name'])}' "
                f"data-label='{_esc(label)}' data-value='{_fmt(val)}'/>")
        parts.append(f"<text x='{gx+gw/2:.1f}' y='{_H-_B+12:.1f}' font-size='9' "
                     f"text-anchor='middle' fill='hsl(222 20% 30%)'>{_esc(label)}</text>")
    parts += _legend(series)
    parts.append("</svg>")
    return "".join(parts)


def bar_svg(chart: ChartSpec, labels: list[str]) -> str:
    flat = ChartSpec(chart.kind, chart.x_label, chart.y_label,
                     [{"name": s["name"], "points": [s["points"][0] if s["points"] else 0.0]}
                      for s in chart.series])
    return grouped_bar_svg(flat, [chart.x_label])


def line_svg(chart: ChartSpec) -> str:
    plot_w, plot_h = _W - _L - _R, _H - _T - _B
    ys = [p[1] for s in chart.series for p in s["points"]] or [1.0]
    xs = [p[0] for s in chart.series for p in s["points"]] or [1.0]
    ymax, xmax = max(ys) or 1.0, max(xs) or 1.0
    parts = _open() + _grid(ymax)
    for si, s in enumerate(chart.series):
        color = _COLORS[si % len(_COLORS)]
        pts = " ".join(f"{_L+(p[0]/xmax)*plot_w:.1f},{_T+plot_h-(p[1]/ymax)*plot_h:.1f}"
                       for p in s["points"])
        parts.append(f"<polyline fill='none' stroke='{color}' stroke-width='2' "
                     f"points='{pts}' data-series='{_esc(s['name'])}'/>")
        for p in s["points"]:
            cx, cy = _L + (p[0]/xmax)*plot_w, _T + plot_h - (p[1]/ymax)*plot_h
            parts.append(f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='3' fill='{color}' "
                         f"data-series='{_esc(s['name'])}' data-label='{_fmt(p[0])}' "
                         f"data-value='{_fmt(p[1])}'/>")
    parts += _legend(chart.series)
    parts.append("</svg>")
    return "".join(parts)
