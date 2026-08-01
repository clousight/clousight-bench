"""Default zero-dependency HTML renderer: a self-contained, shadcn-styled
Clousight-branded document — sticky header, platform overview cards, tabbed
grouped comparison matrix, summary cards, dark mode — with inline SVG charts and
a compact interaction script. No third-party library, no external resource."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from clousight_bench.core.reporting.bundle import Cell, DomainReport, Panel, ReportBundle
from clousight_bench.core.reporting.renderers import brand, svg
from clousight_bench.core.reporting.renderers.base import ReportRenderer
from clousight_bench.core.reporting.renderers.charts_js import CHART_JS
from clousight_bench.core.reporting.renderers.i18n import t, tm

# Canonical tab order; any panel tab not listed is appended after these.
_TAB_ORDER = ["Performance", "Reliability", "Observability", "Cost", "Capability"]
# Headline metrics surfaced on the platform overview cards (first 3 present win).
_HEADLINE = ["cold_start_ms", "cost_usd", "concurrency_knee", "span_completeness",
             "provision_ready_ms"]

_B = brand.BRAND_HSL
_DEFAULT_CSS = f"""
:root{{
--b50:hsl({_B['50']});--b100:hsl({_B['100']});--b200:hsl({_B['200']});
--b400:hsl({_B['400']});--b500:hsl({_B['500']});--b600:hsl({_B['600']});
--b700:hsl({_B['700']});--b900:hsl({_B['900']});
--bg:hsl({brand.BG_HSL});--card:hsl(0 0% 100%);--fg:hsl({brand.FG_HSL});
--muted:hsl(210 28% 96%);--muted-fg:hsl(215 16% 47%);--line:hsl(214 32% 91%);
--primary:hsl(217 71% 51%);--amber:hsl({brand.AMBER_HSL});--red:hsl({brand.RED_HSL});
--font:{brand.FONT_STACK};--radius:.6rem;
--sh-sm:0 1px 2px hsl(217 71% 51% / .06);--sh-md:0 4px 12px hsl(217 71% 51% / .09);
--sh-lg:0 12px 32px hsl(217 71% 51% / .12);}}
@media (prefers-color-scheme:dark){{:root{{--bg:hsl(222 28% 8%);--card:hsl(222 25% 12%);
--fg:hsl(210 40% 95%);--muted:hsl(222 20% 18%);--muted-fg:hsl(215 18% 62%);
--line:hsl(222 20% 22%);--b50:hsl(222 30% 16%);--b100:hsl(222 26% 22%);}}}}
html[lang=zh] .i18n .en{{display:none}} html[lang=en] .i18n .zh{{display:none}}
*{{box-sizing:border-box}}
body{{font-family:var(--font);margin:0;color:var(--fg);background:var(--bg);
font-feature-settings:"ss01";line-height:1.5;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1180px;margin:0 auto;padding:1.6rem 1.5rem 4rem}}
.num{{font-variant-numeric:tabular-nums}}
/* sticky header */
.topbar{{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:.8rem;
padding:.7rem 1.5rem;background:linear-gradient(100deg,var(--b700),var(--b500));
color:#fff;box-shadow:var(--sh-md);backdrop-filter:blur(6px)}}
.topbar img{{height:30px;width:auto}}
.topbar .name{{font-size:1.12rem;font-weight:700;letter-spacing:.2px}}
.topbar .grow{{flex:1}}.topbar .meta{{font-size:.76rem;opacity:.85}}
.toggle{{background:rgba(255,255,255,.16);color:#fff;border:1px solid rgba(255,255,255,.5);
border-radius:.4rem;padding:.32rem .7rem;cursor:pointer;font-family:var(--font);
font-size:.8rem}}
.toggle:hover{{background:rgba(255,255,255,.28)}}
h2{{font-size:1.28rem;margin:1.8rem 0 .3rem}}
h2 small{{color:var(--muted-fg);font-weight:500;font-size:.85rem}}
.section-cap{{color:var(--muted-fg);font-size:.8rem;text-transform:uppercase;
letter-spacing:.08em;margin:1.4rem 0 .5rem;font-weight:600}}
/* cards */
.card{{border:1px solid var(--line);background:var(--card);border-radius:var(--radius);
box-shadow:var(--sh-sm);padding:1rem 1.1rem;margin:.9rem 0;transition:box-shadow .18s}}
.card:hover{{box-shadow:var(--sh-md)}}
.card h3{{margin:.1rem 0 .7rem;font-size:1rem;display:flex;align-items:center;gap:.5rem}}
.card h3 small{{color:var(--muted-fg);font-weight:500;font-size:.72rem}}
/* platform overview cards */
.pcards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:.9rem}}
.pcard{{position:relative;border:1px solid var(--line);background:var(--card);
border-radius:var(--radius);border-top:3px solid var(--primary);padding:.9rem 1rem;
box-shadow:var(--sh-sm);transition:box-shadow .18s,transform .18s}}
.pcard:hover{{box-shadow:var(--sh-lg);transform:translateY(-2px)}}
.pcard .phead{{display:flex;align-items:center;gap:.5rem;margin-bottom:.6rem}}
.pcard .plogo,.pcard .plogo svg{{height:22px;width:auto}}
.pcard .pname{{font-weight:600;font-size:.92rem;flex:1;word-break:break-all}}
.pcard .lchip{{width:22px;height:22px;border-radius:5px;
background:linear-gradient(135deg,var(--b400),var(--b600))}}
.pcard .kv{{display:flex;justify-content:space-between;font-size:.82rem;
padding:.18rem 0;border-top:1px dashed var(--line)}}
.pcard .kv span:first-child{{color:var(--muted-fg)}}
.pcard .kv b{{font-variant-numeric:tabular-nums}}
/* badges */
.badge{{display:inline-block;padding:.05rem .45rem;border-radius:.35rem;font-size:.72rem;
font-weight:600;line-height:1.5}}
.sim{{background:color-mix(in srgb,var(--amber) 18%,transparent);color:var(--amber);
border:1px solid color-mix(in srgb,var(--amber) 40%,transparent)}}
.live{{background:color-mix(in srgb,var(--primary) 15%,transparent);color:var(--primary);
border:1px solid color-mix(in srgb,var(--primary) 40%,transparent)}}
.unknown{{background:var(--muted);color:var(--muted-fg);border:1px solid var(--line)}}
.pill{{display:inline-block;background:var(--b50);color:var(--b700);border-radius:1rem;
padding:.1rem .55rem;font-size:.72rem;font-weight:600;margin:.15rem .25rem .15rem 0}}
/* tabs */
.tabbar{{display:flex;flex-wrap:wrap;gap:.35rem;border-bottom:1px solid var(--line);
margin:1.4rem 0 .3rem}}
.tab{{background:none;border:0;border-bottom:2px solid transparent;color:var(--muted-fg);
font-family:var(--font);font-size:.9rem;font-weight:600;padding:.55rem .9rem;cursor:pointer;
display:flex;align-items:center;gap:.4rem;margin-bottom:-1px}}
.tab:hover{{color:var(--fg)}}
.tab.active{{color:var(--primary);border-bottom-color:var(--primary)}}
.tab .cnt{{background:var(--muted);color:var(--muted-fg);border-radius:1rem;
font-size:.68rem;padding:.02rem .4rem}}
.tab.active .cnt{{background:color-mix(in srgb,var(--primary) 16%,transparent);
color:var(--primary)}}
.tabpanel{{display:none}}
.tabpanel.active{{display:block;animation:fade .3s ease}}
@keyframes fade{{from{{opacity:0;transform:translateY(4px)}}to{{opacity:1;transform:none}}}}
/* grouped comparison table */
.ctable{{border-collapse:collapse;width:100%;font-size:.86rem;margin:.2rem 0 .5rem}}
.ctable th,.ctable td{{padding:.4rem .7rem;text-align:left;border-bottom:1px solid var(--line)}}
.ctable thead th{{position:sticky;top:52px;background:var(--card);color:var(--muted-fg);
font-weight:600;font-size:.78rem}}
.ctable thead th.vcol{{border-top:2px solid var(--primary);color:var(--fg);text-align:right}}
.ctable td.vcol{{text-align:right;font-variant-numeric:tabular-nums}}
.ctable tr.group-header td{{background:color-mix(in srgb,var(--muted) 60%,var(--card));
font-weight:700;font-size:.8rem;color:var(--fg)}}
.ctable tbody tr:hover td{{background:color-mix(in srgb,var(--muted) 45%,transparent)}}
.ctable td.metric{{color:var(--muted-fg)}}
/* misc */
.flag{{background:color-mix(in srgb,var(--red) 8%,var(--card));
border:1px solid color-mix(in srgb,var(--red) 35%,transparent);color:var(--red);
padding:.55rem .8rem;border-radius:.45rem;margin:.4rem 0;font-size:.85rem}}
.legend-item.off{{opacity:.35}}
svg.chart{{max-width:100%;height:auto;margin-top:.4rem}}
.chart-tip{{font-family:var(--font)}}
.summary{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:.9rem}}
/* aggregate column styles */
.agg-col{{background:color-mix(in srgb,var(--primary) 6%,var(--card))}}
.ctable thead th.agg-col{{background:color-mix(in srgb,var(--primary) 10%,var(--card))}}
.agg-sigma{{font-size:.72rem;font-weight:700;color:var(--primary);
background:color-mix(in srgb,var(--primary) 12%,transparent);
border-radius:.3rem;padding:.02rem .35rem;margin-left:.3rem}}
.agg-p95{{display:block;font-size:.74rem;color:var(--muted-fg)}}
.agg-warn{{color:var(--amber);cursor:help;margin-left:.35rem;font-size:.88rem}}
"""


def _esc(s: object) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt(v: object) -> str:
    """Human-readable number: fixed-point (never scientific), thousands grouping,
    up to 8 trimmed decimals. So a tiny cost prints 0.00000249, not 2.49e-06."""
    if isinstance(v, bool):
        return "✓" if v else "✗"
    if isinstance(v, (int, float)):
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return _esc(v)
        try:
            s = format(Decimal(repr(f)).normalize(), "f")
        except (InvalidOperation, ValueError):
            return _esc(v)
        neg = s.startswith("-")
        s = s[1:] if neg else s
        intp, _dot, frac = s.partition(".")
        frac = frac[:8].rstrip("0")
        intp = f"{int(intp):,}" if intp.isdigit() else intp
        out = intp + (f".{frac}" if frac else "")
        return ("-" + out) if neg else out
    return _esc(v)


def _disp(m: dict[str, object] | None) -> str:
    """Format a metric dict's value, appending its currency code when the unit is
    a 3-letter ISO code (USD / CNY / …) so the money unit is explicit."""
    if not m:
        return ""
    v = m["value_num"] if m["value_num"] is not None else m["value_str"]
    s = _fmt(v)
    unit = m.get("unit", "")
    if isinstance(unit, str) and len(unit) == 3 and unit.isalpha() and unit.isupper():
        return f"{s}&nbsp;{_esc(unit)}"
    return s


def _disp_agg(cell: Cell, name: str) -> str:
    """Format an aggregate metric: mean ± stdev with p95 on a second line."""
    if cell.agg_stats is None:
        return _disp(_cell_metric(cell, name))
    stats = cell.agg_stats.get("per_metric", {}).get(name)
    if not stats:
        return ""
    if stats.get("kind") == "numeric":
        mean = stats.get("mean")
        stdev = stats.get("stdev")
        p95 = stats.get("p95")
        mean_s = _fmt(mean) if mean is not None else "·"
        stdev_s = f"&nbsp;&plusmn;&nbsp;{_fmt(stdev)}" if stdev is not None else ""
        p95_s = (f"<span class='agg-p95'>p95&nbsp;{_fmt(p95)}</span>"
                 if p95 is not None else "")
        return f"{mean_s}{stdev_s}{p95_s}"
    mode = stats.get("mode")
    n_val = stats.get("n", cell.agg_stats.get("n", 0))
    return f"{_fmt(mode)}&nbsp;({n_val}/{n_val})" if mode is not None else ""


def _badge(execution: str) -> str:
    cls = {"simulated": "sim", "live": "live"}.get(execution, "unknown")
    return f"<span class='badge {cls}'>{t(execution)}</span>"


def _metric_map(panels: list[Panel]) -> dict[tuple[str, str], dict[str, dict]]:
    """(platform, execution) -> {metric_name: metric_dict} across every panel/cell."""
    out: dict[tuple[str, str], dict[str, dict]] = {}
    for panel in panels:
        for c in panel.cells:
            if c.agg_stats is not None:   # skip aggregate cells
                continue
            bag = out.setdefault((c.platform, c.execution), {})
            for m in c.metrics:
                bag.setdefault(m["name"], m)
    return out


def _platform_card(platform: str, execution: str, metrics: dict[str, dict]) -> str:
    logo = brand.provider_logo(platform)
    mark = f"<span class='plogo'>{logo}</span>" if logo else "<span class='lchip'></span>"
    rows: list[str] = []
    for name in _HEADLINE:
        if name in metrics and len(rows) < 3:
            rows.append(f"<div class='kv'><span>{tm(name)}</span>"
                        f"<b>{_disp(metrics[name])}</b></div>")
    body = "".join(rows) or f"<div class='kv'><span>{t('no data')}</span><b>·</b></div>"
    return (f"<div class='pcard'><div class='phead'>{mark}"
            f"<span class='pname'>{_esc(platform)}</span>{_badge(execution)}</div>{body}</div>")


def _chart_html(panel: Panel, metric_names: list[str]) -> str:
    if not panel.chart:
        return ""
    if panel.chart.kind == "grouped_bar":
        return svg.grouped_bar_svg(panel.chart, metric_names)
    if panel.chart.kind == "bar":
        return svg.bar_svg(panel.chart, metric_names)
    if panel.chart.kind == "line":
        return svg.line_svg(panel.chart)
    return ""


def _panel_html(panel: Panel) -> str:
    individual = [c for c in panel.cells if c.agg_stats is None]
    agg_cells = [c for c in panel.cells if c.agg_stats is not None]

    metric_names: list[str] = []
    for c in individual:
        for m in c.metrics:
            if m["name"] not in metric_names:
                metric_names.append(m["name"])

    ncol = len(individual) + len(agg_cells) + 1

    # Header row
    head_parts = [
        f"<th class='vcol'>{_esc(c.platform)} {_badge(c.execution)}</th>"
        for c in individual
    ]
    for c in agg_cells:
        n = c.agg_stats["n"]
        warn = ""
        if not c.agg_stats.get("comparable", True):
            msgs = c.agg_stats.get("warnings", [])
            tip = _esc(msgs[0]) if msgs else "fingerprint mismatch"
            warn = f"<span class='agg-warn' title='{tip}'>⚠</span>"
        head_parts.append(
            f"<th class='vcol agg-col'>{_esc(c.platform)}"
            f"<span class='agg-sigma'>Σ&nbsp;n={n}</span>{warn}</th>"
        )
    head = "".join(head_parts)

    body = [f"<tr class='group-header'><td colspan='{ncol}'>{t(panel.title)}</td></tr>"]
    for name in metric_names:
        ind_vals = "".join(
            f"<td class='vcol num'>{_disp(_cell_metric(c, name))}</td>"
            for c in individual
        )
        agg_vals = "".join(
            f"<td class='vcol agg-col num'>{_disp_agg(c, name)}</td>"
            for c in agg_cells
        )
        body.append(f"<tr><td class='metric'>{tm(name)}</td>{ind_vals}{agg_vals}</tr>")

    chart = _chart_html(panel, metric_names)
    return (f"<div class='card'><h3>{t(panel.title)}"
            f"<small>[{_esc(panel.evidence)}]</small></h3>{chart}"
            f"<table class='ctable'><thead><tr><th>{t('platform')}</th>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>")


def _cell_metric(cell, name: str) -> dict | None:
    for m in cell.metrics:
        if m["name"] == name:
            return m
    return None


def _capability_card(dom: DomainReport) -> str:
    cols = sorted({p for row in dom.capability_matrix.values() for p in row})
    head = "".join(f"<th class='vcol'>{_esc(p)}</th>" for p in cols)
    ncol = len(cols) + 1
    rows: list[str] = [
        f"<tr class='group-header'><td colspan='{ncol}'>{t('Capability matrix')}</td></tr>"]
    for cap, row in sorted(dom.capability_matrix.items()):
        cells = "".join(f"<td class='vcol'>{_esc(row.get(p, '·'))}</td>" for p in cols)
        rows.append(f"<tr><td class='metric'>{t(cap)}</td>{cells}</tr>")
    return (f"<div class='card'><h3>{t('Capability matrix')}</h3>"
            f"<table class='ctable'><thead><tr><th>{t('capability')}</th>{head}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>")


def _summary_card(platform: str, execution: str, metrics: dict[str, dict],
                  red_flags: list[str]) -> str:
    chips = "".join(f"<span class='pill'>{tm(n)}</span>"
                    for n in _HEADLINE if n in metrics)
    flags = "".join(f"<div class='flag'>{_esc(f)}</div>" for f in red_flags)
    return (f"<div class='pcard'><div class='phead'>"
            f"<span class='pname'>{_esc(platform)}</span>{_badge(execution)}</div>"
            f"<div style='font-size:.82rem;color:var(--muted-fg);margin:.2rem 0'>"
            f"{t('Summary')}</div>{chips or '·'}{flags}</div>")


class HtmlRenderer(ReportRenderer):
    name = "html"
    output_suffix = ".html"

    def render(self, bundle: ReportBundle, *, css: str = "") -> str:
        name = (f"<span class='name i18n'><span class='zh'>{_esc(brand.BRAND_NAME_ZH)}</span>"
                f"<span class='en'>{_esc(brand.BRAND_NAME_EN)}</span></span>")
        toggle = ("<button class='toggle' onclick=\"document.documentElement.lang="
                  "document.documentElement.lang=='en'?'zh':'en'\">中 / EN</button>")
        parts = ["<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>",
                 "<meta name='viewport' content='width=device-width,initial-scale=1'>",
                 f"<title>{t('Clousight Bench report')}</title>",
                 f"<style>{_DEFAULT_CSS}{css}</style></head><body>",
                 f"<div class='topbar'><img src='{brand.logo_data_uri()}' alt='logo'/>"
                 f"{name}<span class='grow'></span>"
                 f"<span class='meta i18n'><span class='zh'>{t('Generated')}</span>"
                 f"<span class='en'>{t('Generated')}</span></span>"
                 f"<span class='meta'>&nbsp;{_esc(bundle.generated_at)}</span>{toggle}</div>",
                 "<div class='wrap'>",
                 f"<p style='color:var(--muted-fg);font-size:.82rem'>{t('Source')}: "
                 f"<code>{_esc(bundle.results_dir)}</code> · {t('Data: locally collected')} · "
                 f"<code>{_esc(bundle.schema)}</code></p>"]
        for dom in bundle.domains:
            parts.append(self._domain_html(dom))
        parts.append(f"</div><script>{CHART_JS}</script></body></html>")
        return "".join(parts)

    def _domain_html(self, dom: DomainReport) -> str:
        mmap = _metric_map(dom.panels)
        out = [f"<h2>{t(dom.domain)} <small>({t(dom.profile)})</small></h2>"]
        for flag in dom.red_flags:
            out.append(f"<div class='flag'>{_esc(flag)}</div>")
        # platform overview cards
        overview = "".join(_platform_card(p, e, mmap[(p, e)]) for (p, e) in sorted(mmap))
        if overview:
            out.append(f"<div class='section-cap'>{t('Overview')}</div>"
                       f"<div class='pcards'>{overview}</div>")
        # tabs
        tabs: list[str] = []
        for p in dom.panels:
            tab = p.tab or "Performance"
            if tab not in tabs:
                tabs.append(tab)
        tabs.sort(key=lambda x: (_TAB_ORDER.index(x) if x in _TAB_ORDER else len(_TAB_ORDER)))
        has_cap = bool(dom.capability_matrix)
        if has_cap and "Capability" not in tabs:
            tabs.append("Capability")
        if not tabs:
            return "".join(out)
        bar, panels = [], []
        for i, tab in enumerate(tabs):
            group = [p for p in dom.panels if (p.tab or "Performance") == tab]
            cnt = len(group) + (1 if tab == "Capability" and has_cap else 0)
            active = " active" if i == 0 else ""
            bar.append(f"<button class='tab{active}' data-tab='{tab}'>{t(tab)}"
                       f"<span class='cnt'>{cnt}</span></button>")
            cards = "".join(_panel_html(p) for p in group)
            if tab == "Capability" and has_cap:
                cards += _capability_card(dom)
            panels.append(f"<div class='tabpanel{active}' data-tab='{tab}'>{cards}</div>")
        out.append(f"<div class='tabbar'>{''.join(bar)}</div>{''.join(panels)}")
        # summary cards
        summary = "".join(
            _summary_card(p, e, mmap[(p, e)], dom.red_flags) for (p, e) in sorted(mmap))
        if summary:
            out.append(f"<div class='section-cap'>{t('Summary')}</div>"
                       f"<div class='summary'>{summary}</div>")
        return "".join(out)
