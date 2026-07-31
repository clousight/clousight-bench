# Reporting

Beyond the markdown comparison (`csbench report`), a local, self-contained HTML
report visualizes every metric and compares same-category cloud products side by
side. It is built in two layers with a stable contract between them:

```
records  ->  ReportBundle (engine, core)  ->  Renderer  ->  report.html
```

- **Engine** (`core/reporting/bundle.py`): assembles a vendor-agnostic,
  JSON-serializable `ReportBundle` — per-(domain, task) panels, a capability
  matrix, cost, and chart specs (data, never rendered SVG). Always core.
- **Profiles** (`core/reporting/profiles/`): declare which panels a category
  shows. `agent-runtime` ships (startup latency, cost list/discount/net,
  elasticity, fault recovery, state, observability); other domains use a generic
  table profile.
- **Renderer** (`core/reporting/renderers/`): turns the bundle into output. The
  default `HtmlRenderer` is pure stdlib — a self-contained document with inline
  SVG charts, no JavaScript, no external resources.

## Generate it

```bash
csbench report --results results --format html --out report.html   # open in a browser
csbench report --results results --dump-bundle bundle.json          # the raw bundle (any tooling)
```

`--format markdown` (the default) is unchanged.

## Cross-vendor comparison + execution hygiene

When two same-category platforms ran (e.g. `aliyun-agentrun` and an AWS agent
runtime), each panel renders them side by side with a comparison chart. **A panel
compares only platforms of the same `execution`** — simulated (mock) and live
(real cloud) records are shown separately and never charted together; a domain
that mixes them shows a red-flag banner. This reuses the fingerprint isolation of
the `execution` marker, so mock numbers can never be mistaken for real ones.

## Customize the rendering

The `ReportBundle` is the stable contract; three ways to change the output
without touching the engine:

- **Theme (`--css file.css`)** — inject CSS appended after the default theme
  (wins), for a logo/colors/layout tweak. Zero dependencies.
- **Renderer plugin** — register a `ReportRenderer` via the
  `clousight_bench.report_renderers` entry point (`name`, `output_suffix`,
  `render(bundle)`), then `--renderer yourname`. Full control (PDF, a different
  HTML, etc.); bring your own dependencies.
- **Template (`--template file.html`)** — render the bundle through a jinja2
  template file (needs the `[report]` extra: `pip install
  clousight-bench[report]`). The template receives `bundle` (the dict form).

The engine, default HTML renderer, inline-SVG charts, `--css`, and
`--dump-bundle` are all pure stdlib; only `--template` needs the extra.
