# Report engine v-echarts — design

Date: 2026-08-16
Status: approved (owner, 2026-08-16)

## Problem

The report engine (`core/reporting/`) turns `ResultRecord` 0.2 files into a
self-contained HTML via a `ReportBundle` model and a hand-authored inline-SVG
renderer. Run against a real full production campaign (`camp-27f20a62`, 27
tasks, aliyun-agentrun) two gaps surfaced:

1. **Time-series data is dropped entirely.** Records carry a `series.$parquet`
   pointer (sha256 + rows). The loader resolves it at the *nested* path
   `agent-runtime/<adapter>/<run-id>/series.parquet`. `csbench fetch` writes the
   parquet *flat* as `<results>/<task_id>.series.parquet`, so `validate_sidecar`
   fails to find it and the loader **skips the whole record**. In the prod run
   this silently dropped the 5 most interesting tasks (T0.1 provision curve,
   T1.1 warm curve, T1.9 TTFT, T1.13 cold→warm convergence, T5.2 elasticity),
   reading 22 of 27.
2. **Only `bar` / `grouped_bar` charts.** No time-series line, no quadrant/
   scatter, no adaptive single-vs-multi-platform layout. Scalar-only.

## Goal

Rebuild the *rendering layer* as an ECharts single-file app while keeping
`ReportBundle` as the vendor-agnostic "what to show" contract. Consume the data
we actually capture — scalars, categorical/evidence marks, **and time-series** —
and present each data shape with the chart type that fits it (quadrant, line,
bar, stacked bar, marked table). Adapt to how many platforms the data holds:
single-platform → per-task deep-dive; multi-platform → cross-platform
comparison.

## Non-goals (YAGNI)

- No SPA / npm / build step. No runtime CDN. The report stays a single
  self-contained offline HTML (archivable, shareable, no external resource).
- No blended cross-dimension score (existing repo principle: per-dimension
  reporting only — blended agent-benchmark rankings have near-zero agreement).
- No new metrics or probes; this is presentation only.

## Approach

Approach A (owner-approved): keep `ReportBundle` as the contract; add a new
`echarts` renderer alongside the existing `html` one; extend the bundle model
and the agent-runtime profile with the new chart kinds and time-series data.

Chart library: **Apache ECharts**, vendored as a single minified UMD asset under
`core/reporting/assets/echarts.min.js` and inlined into the output HTML (~1 MB;
acceptable for a report artifact). Covers time-series (dataZoom), scatter/
quadrant (markLine dividers + quadrant labels), bar/stacked-bar, tooltips,
legend toggle, dark mode. Apache-2.0.

## Data flow

```
ResultRecord 0.2 (JSON)  ─┐
                          ├─► load_records (report.py) ─► build_bundle ─► ReportBundle ─► EchartsRenderer ─► report.html (self-contained)
series.parquet (sidecar) ─┘        │                          │
                          (flat OR nested layout)     (embeds downsampled series)
```

## Components

### 1. Series loading fix (`core/store.py::validate_sidecar` + `core/report.py`)

`validate_sidecar` resolves `results_dir / pointer["$parquet"]`. When that path
does not exist, fall back to `results_dir / f"{task_id}.series.parquet"` (the
`csbench fetch` flat layout) before failing. Integrity is unchanged: the
sha256 and row-count checks still run against whichever file is found, so a
tampered or mismatched sidecar is still rejected. The task_id comes from the
record payload (`identity.task_id`). Net effect: the loader accepts both the
nested run-dir layout and the flat fetch layout; the 5 dropped tasks load.

### 2. Bundle model extensions (`core/reporting/bundle.py`)

- `ChartSpec.kind` gains three values: `"timeseries"`, `"quadrant"`, and
  `"stacked_bar"` (cost list/discount/net). Existing `"bar"` / `"grouped_bar"`
  unchanged; the cost panel switches from `grouped_bar` to `stacked_bar`.
- **Quadrant** `ChartSpec`: `series` carries one point per entity (platform in
  multi mode, task in single mode) as `{name, x, y, meta}`. `x_label` /
  `y_label` name the axes. Optional `x_split` / `y_split` (divider positions,
  default = median of the points) added to `ChartSpec` as new fields.
- **Time-series** data is not a per-panel scalar; add it to `DomainReport` as
  `series: dict[str, dict[str, list[dict]]]` keyed `task_id -> series_name ->
  [{"t": int, "value": float, "unit": str}]`, populated by `build_bundle` from
  the loaded parquet (long table with columns `series, t, value, unit`). A new
  time-series `Panel` references its `task_id`(s); the renderer looks up the
  points from `DomainReport.series`.
- **Downsampling:** series in this domain are tiny (≤ ~50 points). Cap at
  `MAX_SERIES_POINTS = 500`; if exceeded, downsample via `core/rollup.py` and
  `log`/warn the truncation (never silent). Below the cap, embed verbatim.
- **Adaptive mode:** `DomainReport` gains `mode: str` = `"single"` when
  `len(platforms) == 1` else `"multi"`. Renderer branches on it.

Series parquet is read once per results dir at load time and threaded into
`build_bundle` (new optional `series_by_task` argument, mirroring `aggregates`).
`ReportBundle.to_dict()` serializes the new fields so `--dump-bundle` stays the
source of truth for the renderer and for tests.

### 3. Profile: new panels (`core/reporting/profiles/__init__.py`)

Add to `_AGENT_RUNTIME` (declarative `_PanelSpec` list) and extend `_chart()`:

- **Quadrant "Cold-start cost × warm-state performance"** (tab: Performance):
  X = `cold_start_ms`, Y = `warm_start_p50_ms` (fallback `ttft_p50_ms` /
  `warm_steady_ms`). In multi mode one point per platform; in single mode one
  point per contributing task (T1.1, T1.9, T1.13). Dividers at the median of
  each axis. Four quadrant labels (e.g. "fast warm · light cold" = sweet spot).
- **Time-series panels** (kind `timeseries`), one per task that has series:
  - T1.13 `curve_ms` — cold→warm convergence (log Y).
  - T0.1 `provision_ready_ms` — provisioning samples.
  - T1.9 TTFT series.
  - T5.2 `success_rate` and `p95_ms` vs concurrency step (dual axis).
  - T1.1 warm curve.
  A time-series `_PanelSpec` names its `task_ids`; `build_panels` marks the
  panel kind `timeseries` and leaves the points to the renderer via
  `DomainReport.series`.

`_chart()` grows a `quadrant` branch (emit x/y points per cell) and a passthrough
for `timeseries` (the ChartSpec just tags the task_ids; points live on the
DomainReport). Existing bar branches untouched.

Data-shape → chart mapping (the "multi-faceted display"):

| Data shape | Chart |
|---|---|
| cold-start × warm latency | quadrant scatter (markLine dividers, quadrant labels, hover) |
| convergence / provision / TTFT curves | line (log Y + dataZoom) |
| elasticity success_rate & p95 vs concurrency | dual-axis line + bar |
| reliability / observability scalars | grouped_bar / bar |
| capability matrix (support + evidence A/B/C) | marked table (badge-colored; reuses `capability_matrix`) |
| cost list/discount/net | stacked bar |
| all-task status + evidence overview | table + status badges |

### 4. EChartsRenderer (`core/reporting/renderers/echarts.py`)

A `ReportRenderer` (`name = "echarts"`, `output_suffix = ".html"`) that emits ONE
HTML document:

- Inlines `core/reporting/assets/echarts.min.js` (vendored; read at render time
  from the package via `importlib.resources`).
- Serializes the bundle to `window.__BUNDLE__ = {...}` in a `<script>`.
- A compact inline app (~200 lines) that: builds the sticky header + tab nav
  (reuse the existing tab order Performance/Reliability/Observability/Cost/
  Capability), iterates panels, and initializes an ECharts instance per chart
  panel keyed by `chart.kind`; renders table-only panels and the capability
  matrix as styled HTML tables; wires dark mode (`prefers-color-scheme` → an
  ECharts dark theme built from `brand.py` colors) and the existing zh/en i18n
  toggle.
- **No external URL anywhere** (asserted in tests).

`kind → ECharts option` mapping lives in the inline app:
- `timeseries` → `series: line`, `xAxis: category (t)`, `yAxis: log` when the
  values span > 2 orders of magnitude, `dataZoom` inside.
- `quadrant` → `series: scatter`, `markLine` at `x_split`/`y_split`, four
  `graphic` text labels, tooltip showing name + x/y + meta.
- `bar` / `grouped_bar` → bar; `stacked_bar` (cost) → stacked bar.

Reuse `brand.py` (colors), `i18n.py` (`t`/`tm`). The existing `HtmlRenderer`
stays as `--renderer html`.

### 5. CLI wiring (`core/registry.py`, `cli.py`)

- `load_report_renderers()` adds `"echarts": EchartsRenderer()` to the built-in
  dict next to `"html"` (no entry point needed; core owns it).
- `csbench report` default renderer becomes `echarts`; `--renderer html` keeps
  the SVG renderer. `--format markdown` unchanged.

## Testing (TDD, per repo norms)

- `validate_sidecar` finds the sidecar at the flat `<task>.series.parquet` path
  and still rejects sha/row mismatches (both layouts + a tamper case).
- Loader reads a record whose parquet is only present flat (regression for the
  "skipped 5 files" bug) — full 27/27 on the prod fixture.
- `build_bundle` embeds series (task → series → points) and emits `quadrant` +
  `timeseries` ChartSpecs; `mode` is `single` for one platform, `multi` for two.
- Downsample path triggers above `MAX_SERIES_POINTS` and logs the truncation.
- `EchartsRenderer.render(bundle)` produces self-contained HTML: contains the
  inlined ECharts, `window.__BUNDLE__`, every panel title, a quadrant option and
  a timeseries option; contains **no** `http://` / `https://` / `//cdn` URL.
- Golden-ish assertions on the emitted ECharts option JSON for one quadrant and
  one timeseries panel.
- Real-data smoke: `csbench report --results results/prod-camp-27f20a62
  --renderer echarts` renders all 27 tasks (0 skipped) into a valid HTML.

## Rollout

Additive: the `html` renderer and the bundle schema's existing fields are
untouched, so old reports/tests keep working. Bump `BUNDLE_SCHEMA` to `1.1`
(added fields, backward-compatible). Regenerate `results/prod-camp-27f20a62`'s
report as the delivery artifact.
