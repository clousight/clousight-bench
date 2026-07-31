"""Default zero-dependency HTML renderer: self-contained Clousight-branded
document + inline SVG charts + a compact interaction script. No third-party lib."""
from __future__ import annotations

from clousight_bench.core.reporting.bundle import Panel, ReportBundle
from clousight_bench.core.reporting.renderers import brand, svg
from clousight_bench.core.reporting.renderers.base import ReportRenderer
from clousight_bench.core.reporting.renderers.charts_js import CHART_JS
from clousight_bench.core.reporting.renderers.i18n import t, tm

_B = brand.BRAND_HSL
_DEFAULT_CSS = f"""
:root{{
--b50:hsl({_B['50']});--b100:hsl({_B['100']});--b200:hsl({_B['200']});
--b400:hsl({_B['400']});--b500:hsl({_B['500']});--b600:hsl({_B['600']});
--b700:hsl({_B['700']});--b900:hsl({_B['900']});
--bg:hsl({brand.BG_HSL});--fg:hsl({brand.FG_HSL});--muted:hsl(222 12% 45%);
--amber:hsl({brand.AMBER_HSL});--red:hsl({brand.RED_HSL});
--font:{brand.FONT_STACK};--card:#fff;--line:hsl(214 20% 88%);}}
@media (prefers-color-scheme:dark){{:root{{--bg:hsl(222 30% 9%);--fg:hsl(213 30% 92%);
--card:hsl(222 24% 13%);--line:hsl(222 16% 25%);--muted:hsl(213 15% 60%);--b50:hsl(222 30% 15%);}}}}
html[lang=zh] .i18n .en{{display:none}} html[lang=en] .i18n .zh{{display:none}}
body{{font-family:var(--font);margin:0;color:var(--fg);background:var(--bg)}}
.wrap{{margin:1.5rem 2rem}}
h1,h2,h3{{color:var(--b700)}}h2{{border-bottom:2px solid var(--b100);padding-bottom:.2rem}}
.banner{{background:linear-gradient(135deg,var(--b700),var(--b500));color:#fff;
padding:.9rem 2rem;display:flex;align-items:center;gap:.8rem}}
.banner img{{height:32px}}.banner h1{{color:#fff;margin:0;font-size:1.3rem;font-weight:700}}
.banner .grow{{flex:1}}
.toggle{{background:rgba(255,255,255,.18);color:#fff;border:1px solid rgba(255,255,255,.5);
border-radius:.4rem;padding:.3rem .7rem;cursor:pointer;font-family:var(--font)}}
.cards{{display:flex;flex-wrap:wrap;gap:.6rem;margin:.6rem 0}}
.pcard{{display:flex;align-items:center;gap:.5rem;border:1px solid var(--line);
border-radius:.5rem;padding:.4rem .7rem;background:var(--card)}}
.pcard .plogo,.pcard .plogo svg{{height:20px;width:auto}}
.pcard .lchip{{width:20px;height:20px;border-radius:4px;background:var(--b100);
display:inline-block}}
.badge{{display:inline-block;padding:.05rem .4rem;border-radius:.3rem;font-size:.75rem}}
.sim{{background:var(--amber);color:#111}}.live{{background:var(--b600);color:#fff}}
.unknown{{background:var(--line);color:#111}}
table{{border-collapse:collapse;margin:.5rem 0;font-size:.9rem}}
td,th{{border:1px solid var(--line);padding:.25rem .6rem}}th{{background:var(--b50)}}
.flag{{background:hsl(0 80% 96%);border:1px solid hsl(0 60% 80%);color:hsl(0 60% 35%);
padding:.5rem;border-radius:.4rem;margin:.3rem 0}}
.legend-item.off{{opacity:.35}}
svg.chart{{max-width:100%;background:var(--card);border:1px solid var(--line);
border-radius:.5rem;padding:.3rem}}
"""


def _esc(s: object) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _badge(execution: str) -> str:
    cls = {"simulated": "sim", "live": "live"}.get(execution, "unknown")
    return f"<span class='badge {cls}'>{t(execution)}</span>"


def _platform_card(platform: str, execution: str) -> str:
    logo = brand.provider_logo(platform)
    mark = f"<span class='plogo'>{logo}</span>" if logo else "<span class='lchip'></span>"
    return (f"<div class='pcard'>{mark}<b>{_esc(platform)}</b>{_badge(execution)}</div>")


def _panel_html(panel: Panel) -> str:
    metric_names: list[str] = []
    for c in panel.cells:
        for m in c.metrics:
            if m["name"] not in metric_names:
                metric_names.append(m["name"])
    head = "".join(f"<th>{tm(n)}</th>" for n in metric_names)
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
                 f"<div class='banner'><img src='{brand.logo_data_uri()}' alt='logo'/>"
                 f"<h1>{name}</h1><span class='grow'></span>{toggle}</div>",
                 "<div class='wrap'>",
                 f"<p>{t('Data: locally collected')} · {_esc(bundle.results_dir)} · "
                 f"{_esc(bundle.generated_at)} · <code>{_esc(bundle.schema)}</code></p>"]
        for dom in bundle.domains:
            parts.append(f"<h2>{t(dom.domain)} <small>({t(dom.profile)})</small></h2>")
            # platform cards (one execution group per card, provider-branded)
            seen: set[tuple[str, str]] = set()
            cards = []
            for panel in dom.panels:
                for c in panel.cells:
                    key = (c.platform, c.execution)
                    if key not in seen:
                        seen.add(key)
                        cards.append(_platform_card(c.platform, c.execution))
            if cards:
                parts.append(f"<div class='cards'>{''.join(cards)}</div>")
            for flag in dom.red_flags:
                parts.append(f"<p class='flag'>{_esc(flag)}</p>")
            if dom.capability_matrix:
                cols = sorted({p for row in dom.capability_matrix.values() for p in row})
                header = "".join(f"<th>{_esc(p)}</th>" for p in cols)
                parts.append(f"<h3>{t('Capability matrix')}</h3>"
                             f"<table><tr><th>{t('capability')}</th>{header}</tr>")
                for cap, row in sorted(dom.capability_matrix.items()):
                    cells = "".join(f"<td>{_esc(row.get(p, '·'))}</td>" for p in cols)
                    parts.append(f"<tr><td>{t(cap)}</td>{cells}</tr>")
                parts.append("</table>")
            for panel in dom.panels:
                parts.append(_panel_html(panel))
        parts.append(f"</div><script>{CHART_JS}</script></body></html>")
        return "".join(parts)
