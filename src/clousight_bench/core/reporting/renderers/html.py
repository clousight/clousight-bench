"""Default zero-dependency HTML renderer: self-contained document + inline SVG."""
from __future__ import annotations

from clousight_bench.core.reporting.bundle import Panel, ReportBundle
from clousight_bench.core.reporting.renderers import brand, svg
from clousight_bench.core.reporting.renderers.base import ReportRenderer
from clousight_bench.core.reporting.renderers.i18n import t

_DEFAULT_CSS = f"""
:root{{--brand:{brand.BRAND['deep_blue']};--accent:{brand.BRAND['blue']};
--green:{brand.BRAND['green']};--amber:{brand.BRAND['amber']};--red:{brand.BRAND['red']};
--blue50:{brand.BRAND['blue_50']};--bg:#fff;--fg:#1f2937;--muted:#6b7280;
--font-display:{brand.FONT_DISPLAY};--font-body:{brand.FONT_BODY};}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1720;--fg:#e6edf3;--muted:#9aa4b2;--blue50:#16233a;}}}}
html[lang=zh] .i18n .en{{display:none}} html[lang=en] .i18n .zh{{display:none}}
body{{font-family:var(--font-body);margin:0;color:var(--fg);background:var(--bg)}}
.wrap{{margin:2rem}}
h1,h2,h3{{font-family:var(--font-display);color:var(--brand)}}
.banner{{background:linear-gradient(135deg,var(--brand),var(--accent));color:#fff;
padding:1rem 2rem;display:flex;align-items:center;gap:.8rem}}
.banner h1{{color:#fff;margin:0;font-size:1.4rem}}.banner .grow{{flex:1}}
.toggle{{background:rgba(255,255,255,.2);color:#fff;border:1px solid rgba(255,255,255,.5);
border-radius:.4rem;padding:.3rem .6rem;cursor:pointer;font-family:var(--font-body)}}
.badge{{display:inline-block;padding:.1rem .4rem;border-radius:.3rem;font-size:.8rem}}
.sim{{background:var(--amber);color:#000}}.live{{background:var(--green);color:#fff}}
.unknown{{background:#d1d5db;color:#111}}
table{{border-collapse:collapse;margin:.5rem 0}}td,th{{border:1px solid #d1d5db;padding:.25rem .6rem}}
th{{background:var(--blue50)}}
.flag{{background:#FEF2F2;border:1px solid #FCA5A5;color:#991B1B;padding:.5rem;border-radius:.3rem}}
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
    return (f"<h3>{t(panel.title)} <small>[{_esc(panel.evidence)}]</small></h3>"
            f"{chart_html}"
            f"<table><tr><th>{t('platform')}</th>{head}</tr>{''.join(rows)}</table>")


class HtmlRenderer(ReportRenderer):
    name = "html"
    output_suffix = ".html"

    def render(self, bundle: ReportBundle, *, css: str = "") -> str:
        name = (f"<span class='i18n'><span class='zh'>{_esc(brand.BRAND_NAME_ZH)}</span>"
                f"<span class='en'>{_esc(brand.BRAND_NAME_EN)}</span></span>")
        toggle = ("<button class='toggle' onclick=\"document.documentElement.lang="
                  "document.documentElement.lang=='en'?'zh':'en'\">中 / EN</button>")
        parts = ["<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
                 "<title>Clousight Bench report</title>",
                 f"<style>{_DEFAULT_CSS}{css}</style></head><body>",
                 f"<div class='banner'>{brand.LOGO_SVG}<h1>{name}</h1>"
                 f"<span class='grow'></span>{toggle}</div>",
                 "<div class='wrap'>",
                 f"<p>{t('Data: locally collected')} · {_esc(bundle.results_dir)} · "
                 f"{_esc(bundle.generated_at)} · <code>{_esc(bundle.schema)}</code></p>"]
        for dom in bundle.domains:
            parts.append(f"<h2>{_esc(dom.domain)} <small>({_esc(dom.profile)})</small></h2>")
            for flag in dom.red_flags:
                parts.append(f'<p class="flag">{_esc(flag)}</p>')
            if dom.capability_matrix:
                cols = sorted({p for row in dom.capability_matrix.values() for p in row})
                header = "".join(f"<th>{_esc(p)}</th>" for p in cols)
                parts.append(f"<h3>{t('Capability matrix')}</h3>"
                             f"<table><tr><th>{t('capability')}</th>{header}</tr>")
                for cap, row in sorted(dom.capability_matrix.items()):
                    cells = "".join(f"<td>{_esc(row.get(p, '·'))}</td>" for p in cols)
                    parts.append(f"<tr><td>{_esc(cap)}</td>{cells}</tr>")
                parts.append("</table>")
            for panel in dom.panels:
                parts.append(_panel_html(panel))
        parts.append("</div></body></html>")
        return "".join(parts)
