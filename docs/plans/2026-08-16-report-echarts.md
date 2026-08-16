# Report engine v-echarts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the report render layer as an ECharts single-file app that consumes scalars, categorical/evidence marks, and time-series, presenting each with a fitting chart (quadrant, line, bar, stacked bar, marked table), adaptive to single vs multi platform.

**Architecture:** Keep `ReportBundle` as the vendor-agnostic "what to show" contract. Fix the sidecar loader so flat-fetched parquet loads. Extend the bundle + agent-runtime profile with `quadrant` / `timeseries` / `stacked_bar` chart kinds, embedded downsampled series, and a `single|multi` mode. Add a new `echarts` renderer next to the existing `html` one; make it the default.

**Tech Stack:** Python 3.12, pyarrow (already a dep for the `[store]` extra / series), Apache ECharts 5.x (vendored min.js, inlined), pytest.

## Global Constraints

- Additive only: the `html` renderer, `HtmlRenderer.render(bundle, *, css="")`, and every existing bundle field stay working. Bump `BUNDLE_SCHEMA` to `"report-bundle/1.1"`.
- Output is a SINGLE self-contained offline HTML: **no** `http://` / `https://` / `//cdn` URL anywhere in the emitted document. ECharts is inlined from a vendored asset.
- No blended cross-dimension score. Per-dimension only.
- Never mix `simulated` and `live` in one chart (existing rule; quadrant/timeseries must group by execution).
- Chinese for narration; English only in code/comments/commit messages.
- Downsample cap `MAX_SERIES_POINTS = 500`; above it, downsample and `warnings.warn` / stderr-log the truncation — never silent.
- Run tests with `.venv/bin/pytest`. Commit after each task.

---

## File Structure

- `src/clousight_bench/core/store.py` — MODIFY `validate_sidecar` (flat-layout fallback).
- `src/clousight_bench/core/report.py` — ADD `_load_series(results_dir)`.
- `src/clousight_bench/core/reporting/bundle.py` — MODIFY `ChartSpec`, `DomainReport`, `build_bundle`; ADD quadrant helper wiring.
- `src/clousight_bench/core/reporting/profiles/__init__.py` — ADD quadrant panel, timeseries panels, stacked_bar cost, `_quadrant_panel`, `build_timeseries_panels`.
- `src/clousight_bench/core/reporting/assets/echarts.min.js` — NEW vendored asset.
- `src/clousight_bench/core/reporting/renderers/echarts.py` — NEW `EchartsRenderer`.
- `src/clousight_bench/core/registry.py` — MODIFY `load_report_renderers` (register `echarts`).
- `src/clousight_bench/cli.py` — MODIFY `_report_bundle` (load + pass series); MODIFY report argparse (`--renderer` default `echarts`, `--format` default `html`).
- `pyproject.toml` — MODIFY package-data to ship `reporting/assets/*.js`.
- Tests: `tests/test_store.py`, `tests/test_report.py`, `tests/test_report_bundle.py`, `tests/test_report_profiles.py`, `tests/test_report_echarts.py` (new), `tests/test_cli_report_html.py`.

---

## Task 1: Sidecar flat-layout fallback

**Files:**
- Modify: `src/clousight_bench/core/store.py` (`validate_sidecar`, ~line 96)
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: existing `validate_sidecar(results_dir: Path, payload: dict) -> tuple[Path|None, str|None]`.
- Produces: same signature; now also finds `<results_dir>/<task_id>.series.parquet` when the pointer path is absent. `task_id` read from `payload["identity"]["task_id"]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store.py
def test_validate_sidecar_accepts_flat_fetch_layout(tmp_path):
    import hashlib
    import pyarrow as pa
    import pyarrow.parquet as pq
    from clousight_bench.core.store import validate_sidecar

    tbl = pa.table({"series": ["m"], "t": [1], "value": [1.0], "unit": [""]})
    flat = tmp_path / "T9.9.series.parquet"
    pq.write_table(tbl, flat)
    data = flat.read_bytes()
    payload = {
        "identity": {"task_id": "T9.9"},
        # pointer names the NESTED path that fetch did not create:
        "series": {
            "$parquet": "agent-runtime/x/run-1/series.parquet",
            "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            "rows": 1,
        },
    }
    path, err = validate_sidecar(tmp_path, payload)
    assert err is None
    assert path == flat.resolve()


def test_validate_sidecar_flat_still_checks_sha(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from clousight_bench.core.store import validate_sidecar

    tbl = pa.table({"series": ["m"], "t": [1], "value": [1.0], "unit": [""]})
    pq.write_table(tbl, tmp_path / "T9.9.series.parquet")
    payload = {
        "identity": {"task_id": "T9.9"},
        "series": {"$parquet": "nope/series.parquet", "sha256": "sha256:deadbeef", "rows": 1},
    }
    _, err = validate_sidecar(tmp_path, payload)
    assert err == "sidecar sha256 mismatch"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_store.py -k flat -v`
Expected: FAIL (`sidecar unreadable: ...`).

- [ ] **Step 3: Implement the fallback**

In `validate_sidecar`, after computing `path = (root / relpath).resolve()` and the `is_relative_to` check, before `path.read_bytes()`, add a fallback when the pointer path is missing:

```python
    if not path.exists():
        task_id = payload.get("identity", {}).get("task_id")
        if isinstance(task_id, str) and task_id:
            flat = (root / f"{task_id}.series.parquet").resolve()
            if flat.is_relative_to(root) and flat.exists():
                path = flat
```

The existing `read_bytes` + sha256 + row-count checks then run against `path` unchanged (integrity preserved for either layout).

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_store.py -k "flat or sidecar" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clousight_bench/core/store.py tests/test_store.py
git commit -m "fix(report): resolve series sidecar from the flat fetch layout"
```

---

## Task 2: Bundle model — chart kinds, mode, embedded series

**Files:**
- Modify: `src/clousight_bench/core/reporting/bundle.py`
- Test: `tests/test_report_bundle.py`

**Interfaces:**
- Produces:
  - `ChartSpec(kind, x_label, y_label, series, x_split: float|None=None, y_split: float|None=None)` with those two new optional fields serialized in `to_dict`.
  - `DomainReport(..., red_flags=[], mode: str="multi", series: dict[str,dict[str,list[dict]]]=field(default_factory=dict))` — `mode` and `series` serialized.
  - `build_bundle(records, *, results_dir, generated_at, profiles, aggregates=None, series_by_task=None)`. `series_by_task: dict[str, dict[str, list[dict]]] | None` keyed `task_id -> series_name -> [{"t","value","unit"}]`.
  - `BUNDLE_SCHEMA = "report-bundle/1.1"`.
  - Consumed by Task 4 (`build_timeseries_panels`), Task 6 (renderer reads `mode`, `series`, `x_split`/`y_split`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report_bundle.py
def test_bundle_mode_and_series(report_record):
    from clousight_bench.core.reporting.bundle import build_bundle, BUNDLE_SCHEMA
    from clousight_bench.core.reporting.profiles import PROFILES

    assert BUNDLE_SCHEMA == "report-bundle/1.1"
    rec = report_record("aliyun-agentrun", "T1.13", execution="live",
                         measurements={"cold_start_ms": 87000.0})
    series = {"T1.13": {"curve_ms": [{"t": 1, "value": 87000.0, "unit": ""},
                                     {"t": 2, "value": 70.0, "unit": ""}]}}
    b = build_bundle([rec], results_dir="r", generated_at="t",
                     profiles=PROFILES, series_by_task=series)
    dom = b.domains[0]
    assert dom.mode == "single"
    assert dom.series["T1.13"]["curve_ms"][0]["value"] == 87000.0
    d = b.to_dict()  # still JSON-serializable
    assert d["domains"][0]["mode"] == "single"
    assert d["domains"][0]["series"]["T1.13"]["curve_ms"][1]["value"] == 70.0


def test_chartspec_split_serialized():
    from clousight_bench.core.reporting.bundle import ChartSpec
    c = ChartSpec(kind="quadrant", x_label="x", y_label="y",
                  series=[{"name": "p", "x": 1.0, "y": 2.0, "meta": {}}],
                  x_split=1.0, y_split=2.0)
    assert c.to_dict()["x_split"] == 1.0 and c.to_dict()["y_split"] == 2.0
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest tests/test_report_bundle.py -k "mode_and_series or split" -v`
Expected: FAIL (`TypeError` on unknown kwargs / `BUNDLE_SCHEMA` mismatch).

- [ ] **Step 3: Implement**

In `bundle.py`:
- `BUNDLE_SCHEMA = "report-bundle/1.1"`.
- `ChartSpec`: add `x_split: float | None = None` and `y_split: float | None = None` fields; in `to_dict` add `"x_split": self.x_split, "y_split": self.y_split`.
- `DomainReport`: add `mode: str = "multi"` and `series: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)` after `red_flags`; in `to_dict` add `"mode": self.mode, "series": self.series`.
- `build_bundle`: add `series_by_task=None` parameter. Inside the per-domain loop set `mode = "single" if len(platforms) == 1 else "multi"`, and pass series for this domain's tasks:

```python
        dom_tasks = {r.identity.task_id for r in recs}
        dom_series = {
            tid: s for tid, s in (series_by_task or {}).items() if tid in dom_tasks
        }
        domains.append(
            DomainReport(domain, profile.name, platforms, cap, panels, red_flags,
                         mode=mode, series=dom_series)
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_report_bundle.py -v`
Expected: PASS (existing bundle tests still green).

- [ ] **Step 5: Commit**

```bash
git add src/clousight_bench/core/reporting/bundle.py tests/test_report_bundle.py
git commit -m "feat(report): bundle carries mode + embedded series + quadrant splits (schema 1.1)"
```

---

## Task 3: Load series from parquet into the bundle

**Files:**
- Modify: `src/clousight_bench/core/report.py` (add `_load_series`)
- Modify: `src/clousight_bench/cli.py` (`_report_bundle`)
- Test: `tests/test_report.py`

**Interfaces:**
- Produces: `_load_series(results_dir: Path) -> dict[str, dict[str, list[dict]]]` — globs `*.series.parquet` + `**/series.parquet` under `results_dir`, reads columns `task_id, series, t, value, unit`, returns `{task_id: {series: [{"t","value","unit"}, ... sorted by t]}}`. Above `MAX_SERIES_POINTS` per series, downsample (stride) and warn.
- Consumed by `_report_bundle` → `build_bundle(series_by_task=...)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report.py
def test_load_series_reads_flat_and_nested(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from clousight_bench.core.report import _load_series

    def write(p, task):
        pq.write_table(pa.table({
            "task_id": [task, task], "series": ["curve_ms", "curve_ms"],
            "t": [1, 2], "value": [87000.0, 70.0], "unit": ["", ""],
        }), p)
    write(tmp_path / "T1.13.series.parquet", "T1.13")
    nested = tmp_path / "agent-runtime" / "x" / "run-1"
    nested.mkdir(parents=True)
    write(nested / "series.parquet", "T0.1")

    got = _load_series(tmp_path)
    assert got["T1.13"]["curve_ms"] == [
        {"t": 1, "value": 87000.0, "unit": ""}, {"t": 2, "value": 70.0, "unit": ""}]
    assert "T0.1" in got
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest tests/test_report.py -k load_series -v`
Expected: FAIL (`ImportError` / `AttributeError`).

- [ ] **Step 3: Implement `_load_series`**

Add to `report.py`:

```python
MAX_SERIES_POINTS = 500


def _load_series(results_dir: Path) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Read every *.series.parquet (flat fetch layout) and **/series.parquet
    (nested run layout) into {task_id: {series: [{"t","value","unit"}]}}."""
    import pyarrow.parquet as pq

    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    seen: set[Path] = set()
    for pattern in ("*.series.parquet", "**/series.parquet"):
        for path in sorted(Path(results_dir).glob(pattern)):
            rp = path.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            try:
                tbl = pq.read_table(path, columns=["task_id", "series", "t", "value", "unit"])
            except (OSError, ValueError, KeyError):
                continue
            for row in tbl.to_pylist():
                tid, sname = str(row["task_id"]), str(row["series"])
                out.setdefault(tid, {}).setdefault(sname, []).append(
                    {"t": row["t"], "value": row["value"], "unit": row.get("unit") or ""}
                )
    for tid, byname in out.items():
        for sname, pts in byname.items():
            pts.sort(key=lambda p: p["t"])
            if len(pts) > MAX_SERIES_POINTS:
                stride = len(pts) // MAX_SERIES_POINTS + 1
                print(
                    f"clousight-bench: downsampled {tid}/{sname} "
                    f"{len(pts)}->{len(pts[::stride])} points",
                    file=sys.stderr,
                )
                byname[sname] = pts[::stride]
    return out
```

- [ ] **Step 4: Wire into `_report_bundle` (cli.py)**

```python
    from clousight_bench.core.report import _load_results, _load_series
    ...
    records = _load_results(results_path)
    aggregates = _load_aggregates(results_path)
    series_by_task = _load_series(results_path)
    return build_bundle(
        records, results_dir=str(results_dir),
        generated_at=_dt.datetime.now().isoformat(timespec="seconds"),
        profiles=PROFILES, aggregates=aggregates, series_by_task=series_by_task,
    )
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/pytest tests/test_report.py -k load_series -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/clousight_bench/core/report.py src/clousight_bench/cli.py tests/test_report.py
git commit -m "feat(report): load series parquet (flat+nested) into the bundle"
```

---

## Task 4: Profile — quadrant + timeseries + stacked-bar panels

**Files:**
- Modify: `src/clousight_bench/core/reporting/profiles/__init__.py`
- Modify: `src/clousight_bench/core/reporting/bundle.py` (call `build_timeseries_panels` in `build_bundle`)
- Test: `tests/test_report_profiles.py`

**Interfaces:**
- Produces:
  - `_quadrant_panel(latest: dict) -> list[Panel]` — one Panel per execution, `chart.kind == "quadrant"`. X=`cold_start_ms`; Y=first present of `["warm_start_p50_ms","ttft_p50_ms","warm_steady_ms"]`. One point per (task, platform) that has both, `{"name": f"{platform}·{task}", "x", "y", "meta": {"platform","task"}}`. `x_split`/`y_split` = median of the points. Appended by `Profile.build_panels`.
  - `build_timeseries_panels(series_by_task: dict) -> list[Panel]` — one Panel per configured task present in series, `chart.kind == "timeseries"`, `task_ids=[task]`, empty `cells`. Uses `_TIMESERIES_TASKS`.
  - Cost panel `key="cost"` chart kind changed `grouped_bar` → `stacked_bar`.
- Consumed by Task 6 renderer.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report_profiles.py
def test_quadrant_panel_points(report_record):
    from clousight_bench.core.reporting.profiles import PROFILES
    latest = {
        ("T1.13", "aliyun-agentrun", "live"): report_record(
            "aliyun-agentrun", "T1.13", execution="live",
            measurements={"cold_start_ms": 87000.0, "warm_start_p50_ms": 70.0}),
        ("T1.1", "aliyun-agentrun", "live"): report_record(
            "aliyun-agentrun", "T1.1", execution="live",
            measurements={"cold_start_ms": 6000.0, "warm_start_p50_ms": 40.0}),
    }
    panels = PROFILES["agent-runtime"].build_panels(latest)
    q = [p for p in panels if p.chart and p.chart.kind == "quadrant"][0]
    xs = sorted(pt["x"] for pt in q.chart.series)
    assert xs == [6000.0, 87000.0]
    assert q.chart.x_split == 46500.0  # median of 2 points


def test_timeseries_panels_from_series():
    from clousight_bench.core.reporting.profiles import build_timeseries_panels
    panels = build_timeseries_panels({"T1.13": {"curve_ms": [{"t": 1, "value": 1.0, "unit": ""}]}})
    assert len(panels) == 1
    assert panels[0].chart.kind == "timeseries"
    assert panels[0].task_ids == ["T1.13"]


def test_cost_panel_is_stacked_bar(report_record):
    from clousight_bench.core.reporting.profiles import PROFILES
    latest = {("T5.1", "aliyun-agentrun", "live"): report_record(
        "aliyun-agentrun", "T5.1", execution="live",
        measurements={"list_cost_usd": 1.0, "discount_usd": 0.2, "cost_usd": 0.8})}
    panels = PROFILES["agent-runtime"].build_panels(latest)
    cost = [p for p in panels if p.key == "cost"][0]
    assert cost.chart.kind == "stacked_bar"
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest tests/test_report_profiles.py -k "quadrant or timeseries or stacked" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `profiles/__init__.py`:

```python
import statistics

_QUADRANT_X = "cold_start_ms"
_QUADRANT_Y = ["warm_start_p50_ms", "ttft_p50_ms", "warm_steady_ms"]

# task_id -> (tab, title). Only emitted when series data exists for the task.
_TIMESERIES_TASKS: dict[str, tuple[str, str]] = {
    "T1.13": ("Performance", "Cold→warm convergence"),
    "T0.1": ("Performance", "Provisioning samples"),
    "T1.9": ("Performance", "Time-to-first-token"),
    "T5.2": ("Capability", "Elasticity under load"),
    "T1.1": ("Performance", "Warm-start curve"),
}


def _num(rec, key):
    m = rec.measurements.get(key)
    v = m.get("value") if isinstance(m, dict) else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _quadrant_panel(latest: dict) -> list[Panel]:
    by_exec: dict[str, list[dict]] = {}
    for (task, platform, execu), rec in latest.items():
        if rec.status not in ("completed", "unsupported"):
            continue
        x = _num(rec, _QUADRANT_X)
        y = next((_num(rec, k) for k in _QUADRANT_Y if _num(rec, k) is not None), None)
        if x is None or y is None:
            continue
        by_exec.setdefault(execu, []).append(
            {"name": f"{platform}·{task}", "x": x, "y": y,
             "meta": {"platform": platform, "task": task}})
    panels: list[Panel] = []
    for execu, pts in by_exec.items():
        if not pts:
            continue
        xs = statistics.median(p["x"] for p in pts)
        ys = statistics.median(p["y"] for p in pts)
        chart = ChartSpec(kind="quadrant", x_label="cold_start_ms",
                          y_label="warm p50 (ms)", series=pts, x_split=xs, y_split=ys)
        cells = [Cell(platform="", status="completed", execution=execu, metrics=[])]
        panels.append(Panel("quadrant", "Cold-start cost × warm-state performance",
                            "B", list({p["meta"]["task"] for p in pts}), cells,
                            chart, comparison=len(pts) > 1, tab="Performance"))
    return panels


def build_timeseries_panels(series_by_task: dict) -> list[Panel]:
    panels: list[Panel] = []
    for task, (tab, title) in _TIMESERIES_TASKS.items():
        if not series_by_task.get(task):
            continue
        chart = ChartSpec(kind="timeseries", x_label="step", y_label="value", series=[])
        panels.append(Panel(f"ts_{task}", title, "B", [task], [], chart, tab=tab))
    return panels
```

Append the quadrant in `Profile.build_panels` just before `return panels`:

```python
        panels.extend(_quadrant_panel(latest))
        return panels
```

Change the cost `_PanelSpec` chart kind from `"grouped_bar"` to `"stacked_bar"` (the `key="cost"` row). Add a `stacked_bar` branch to `_chart` that behaves exactly like `grouped_bar` (same series shape; the kind string differs so the renderer stacks):

```python
    if kind in ("bar", "grouped_bar", "stacked_bar"):
        ... existing bar body ...
        return ChartSpec(kind=kind, ...)
```

In `bundle.py` `build_bundle`, after `panels = profile.build_panels(latest)` and the aggregate-cell loop, append timeseries panels:

```python
        from clousight_bench.core.reporting.profiles import build_timeseries_panels
        panels.extend(build_timeseries_panels(dom_series))
```

(`dom_series` from Task 2.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_report_profiles.py tests/test_report_bundle.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clousight_bench/core/reporting/profiles/__init__.py src/clousight_bench/core/reporting/bundle.py tests/test_report_profiles.py
git commit -m "feat(report): quadrant, timeseries, and stacked-bar cost panels"
```

---

## Task 5: Vendor the ECharts asset

**Files:**
- Create: `src/clousight_bench/core/reporting/assets/echarts.min.js`
- Create: `src/clousight_bench/core/reporting/assets/__init__.py` (empty; makes it importable resource dir)
- Modify: `pyproject.toml` (ship the asset)

- [ ] **Step 1: Download the pinned asset**

```bash
mkdir -p src/clousight_bench/core/reporting/assets
touch src/clousight_bench/core/reporting/assets/__init__.py
NO_PROXY='*' curl -fsSL -o src/clousight_bench/core/reporting/assets/echarts.min.js \
  https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js
test $(wc -c < src/clousight_bench/core/reporting/assets/echarts.min.js) -gt 900000
head -c 60 src/clousight_bench/core/reporting/assets/echarts.min.js   # sanity: license banner
```

Expected: file ~1 MB, starts with the ECharts license comment.

- [ ] **Step 2: Ship it in the package**

In `pyproject.toml`, ensure package data includes the asset. For setuptools add/extend:

```toml
[tool.setuptools.package-data]
"clousight_bench.core.reporting.assets" = ["*.js"]
```

(If the project uses `hatchling` or similar, add the equivalent include glob for `src/clousight_bench/core/reporting/assets/*.js`. Check `[build-system]` first and match it.)

- [ ] **Step 3: Verify it loads as a package resource**

Run:
```bash
.venv/bin/python -c "import importlib.resources as r; \
print(len(r.files('clousight_bench.core.reporting.assets').joinpath('echarts.min.js').read_text(encoding='utf-8')))"
```
Expected: prints a number > 900000.

- [ ] **Step 4: Commit**

```bash
git add src/clousight_bench/core/reporting/assets pyproject.toml
git commit -m "chore(report): vendor echarts 5.5.1 min.js as a package asset"
```

---

## Task 6: EchartsRenderer

**Files:**
- Create: `src/clousight_bench/core/reporting/renderers/echarts.py`
- Modify: `src/clousight_bench/core/registry.py` (`load_report_renderers`)
- Test: `tests/test_report_echarts.py` (new)

**Interfaces:**
- Consumes: `ReportBundle` (with `mode`, `series`, panels whose `chart.kind` ∈ {bar, grouped_bar, stacked_bar, quadrant, timeseries}).
- Produces: `EchartsRenderer(ReportRenderer)` with `name = "echarts"`, `output_suffix = ".html"`, `render(self, bundle) -> str`. Registered as `"echarts"` in `load_report_renderers`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report_echarts.py
import re
from clousight_bench.core.reporting.bundle import build_bundle
from clousight_bench.core.reporting.profiles import PROFILES
from clousight_bench.core.reporting.renderers.echarts import EchartsRenderer


def _bundle(report_record):
    rec = report_record("aliyun-agentrun", "T1.13", execution="live",
                        measurements={"cold_start_ms": 87000.0, "warm_start_p50_ms": 70.0})
    series = {"T1.13": {"curve_ms": [{"t": 1, "value": 87000.0, "unit": ""},
                                     {"t": 2, "value": 70.0, "unit": ""}]}}
    return build_bundle([rec], results_dir="r", generated_at="t",
                        profiles=PROFILES, series_by_task=series)


def test_render_is_self_contained(report_record):
    html = EchartsRenderer().render(_bundle(report_record))
    assert "window.__BUNDLE__" in html
    assert "echarts" in html.lower()
    # ECharts inlined (big), and NO external resource:
    assert len(html) > 900_000
    assert not re.search(r"https?://", html)
    assert "//cdn" not in html


def test_render_has_chart_kinds(report_record):
    html = EchartsRenderer().render(_bundle(report_record))
    assert "quadrant" in html and "timeseries" in html


def test_renderer_registered():
    from clousight_bench.core.registry import load_report_renderers
    assert "echarts" in load_report_renderers()
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest tests/test_report_echarts.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the renderer**

Create `renderers/echarts.py`. Read the vendored asset via `importlib.resources`, embed the bundle JSON, and ship one inline app that maps `chart.kind` → an ECharts option. Group charts by tab; render table-only panels + the capability matrix as HTML tables; dark mode from `prefers-color-scheme`.

```python
"""EChartsRenderer: a single self-contained HTML report. Inlines the vendored
ECharts UMD, embeds the ReportBundle as window.__BUNDLE__, and renders each
panel with an ECharts instance chosen by chart.kind (bar/grouped_bar/
stacked_bar/quadrant/timeseries) plus HTML tables for scalar/capability panels.
No external resource."""

from __future__ import annotations

import importlib.resources as resources
import json

from clousight_bench.core.reporting.bundle import ReportBundle
from clousight_bench.core.reporting.renderers import brand
from clousight_bench.core.reporting.renderers.base import ReportRenderer

_TAB_ORDER = ["Performance", "Reliability", "Observability", "Cost", "Capability"]


def _echarts_js() -> str:
    return (
        resources.files("clousight_bench.core.reporting.assets")
        .joinpath("echarts.min.js")
        .read_text(encoding="utf-8")
    )


_APP_JS = r"""
(function(){
  var B = window.__BUNDLE__;
  var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  var PAL = ['#2f6df6','#f59e0b','#ef4444','#10b981','#8b5cf6','#14b8a6'];
  function el(tag, cls, txt){var e=document.createElement(tag); if(cls)e.className=cls;
    if(txt!=null)e.textContent=txt; return e;}
  function optBar(chart, stacked){
    var names = chart.series.length ? chart.series.length : 0;
    var cats = chart.x_label.split(' / ');
    var series = chart.series.map(function(s){
      return {name:s.name, type:'bar', stack: stacked?'all':undefined, data:s.points};});
    return {tooltip:{trigger:'axis'}, legend:{}, xAxis:{type:'category', data:cats},
            yAxis:{type:'value', name:chart.y_label}, series:series};
  }
  function optQuadrant(chart){
    var pts = chart.series.map(function(p){return {name:p.name, value:[p.x,p.y]};});
    return {tooltip:{formatter:function(o){return o.data.name+'<br>'+chart.x_label+': '+
              o.data.value[0]+'<br>'+chart.y_label+': '+o.data.value[1];}},
      xAxis:{type:'value', name:chart.x_label, scale:true},
      yAxis:{type:'value', name:chart.y_label, scale:true},
      series:[{type:'scatter', symbolSize:14, data:pts,
        markLine:{silent:true, lineStyle:{type:'dashed'}, data:[
          {xAxis:chart.x_split},{yAxis:chart.y_split}]}}]};
  }
  function optTimeseries(task, byname){
    var names = Object.keys(byname);
    var maxv=0, minv=Infinity;
    names.forEach(function(n){byname[n].forEach(function(p){
      maxv=Math.max(maxv,p.value); if(p.value>0)minv=Math.min(minv,p.value);});});
    var logy = maxv>0 && minv>0 && (maxv/minv) > 100;
    var series = names.map(function(n){return {name:n, type:'line', smooth:true,
      data: byname[n].map(function(p){return [p.t, p.value];})};});
    return {tooltip:{trigger:'axis'}, legend:{}, dataZoom:[{type:'inside'},{type:'slider'}],
      xAxis:{type:'value', name:'step'}, yAxis:{type: logy?'log':'value'}, series:series};
  }
  function mkChart(host, option){
    var c = echarts.init(host, dark?'dark':null, {renderer:'canvas'});
    c.setOption(option); window.addEventListener('resize', function(){c.resize();});
  }
  function tableFor(panel){
    var t = el('table','tbl'); var seen={}; 
    panel.cells.forEach(function(c){c.metrics.forEach(function(m){seen[m.name]=1;});});
    var keys = Object.keys(seen);
    var hr = el('tr'); hr.appendChild(el('th','', 'platform'));
    keys.forEach(function(k){hr.appendChild(el('th','',k));}); t.appendChild(hr);
    panel.cells.forEach(function(c){var r=el('tr'); r.appendChild(el('td','',c.platform||c.execution));
      var byn={}; c.metrics.forEach(function(m){byn[m.name]=m.value_num!=null?m.value_num:m.value_str;});
      keys.forEach(function(k){r.appendChild(el('td','num', byn[k]!=null?String(byn[k]):'·'));});
      t.appendChild(r);}); return t;
  }
  function capMatrix(dom){
    var m=dom.capability_matrix; var t=el('table','tbl'); var plats=dom.platforms;
    var hr=el('tr'); hr.appendChild(el('th','','capability'));
    plats.forEach(function(p){hr.appendChild(el('th','',p));}); t.appendChild(hr);
    Object.keys(m).forEach(function(cap){var r=el('tr'); r.appendChild(el('td','',cap));
      plats.forEach(function(p){r.appendChild(el('td','',m[cap][p]||'·'));}); t.appendChild(r);});
    return t;
  }
  var root = document.getElementById('app');
  B.domains.forEach(function(dom){
    root.appendChild(el('h2','', dom.domain+' ('+dom.mode+')'));
    (dom.red_flags||[]).forEach(function(f){root.appendChild(el('div','flag',f));});
    var byTab={}; dom.panels.forEach(function(p){(byTab[p.tab||'Other']=byTab[p.tab||'Other']||[]).push(p);});
    var tabs = TAB_ORDER.filter(function(t){return byTab[t];})
      .concat(Object.keys(byTab).filter(function(t){return TAB_ORDER.indexOf(t)<0;}));
    tabs.forEach(function(tab){
      root.appendChild(el('h3','tabhdr',tab));
      byTab[tab].forEach(function(p){
        var card=el('section','card'); card.appendChild(el('h4','',p.title));
        var ch=p.chart;
        if(ch && ch.kind==='timeseries'){
          var host=el('div','chart'); card.appendChild(host); root.appendChild(card);
          mkChart(host, optTimeseries(p.task_ids[0], dom.series[p.task_ids[0]]||{})); return;
        }
        if(ch && ch.kind==='quadrant'){
          var host=el('div','chart'); card.appendChild(host); root.appendChild(card);
          mkChart(host, optQuadrant(ch)); return;
        }
        if(ch && (ch.kind==='bar'||ch.kind==='grouped_bar'||ch.kind==='stacked_bar')){
          var host=el('div','chart'); card.appendChild(host); root.appendChild(card);
          mkChart(host, optBar(ch, ch.kind==='stacked_bar')); return;
        }
        card.appendChild(tableFor(p)); root.appendChild(card);
      });
    });
    root.appendChild(el('h3','tabhdr','Capability matrix'));
    var cap=el('section','card'); cap.appendChild(capMatrix(dom)); root.appendChild(cap);
  });
})();
""".replace("TAB_ORDER", "TAB_ORDER")


class EchartsRenderer(ReportRenderer):
    name = "echarts"
    output_suffix = ".html"

    def render(self, bundle: ReportBundle) -> str:
        data = json.dumps(bundle.to_dict(), ensure_ascii=False)
        css = (
            "body{font-family:system-ui,'Noto Sans SC',sans-serif;margin:0;"
            "background:#f7f9fc;color:#0f172a}@media(prefers-color-scheme:dark)"
            "{body{background:#0b1220;color:#e2e8f0}}"
            ".wrap{max-width:1180px;margin:0 auto;padding:1.5rem}"
            ".card{background:var(--card,#fff);border:1px solid #e2e8f0;border-radius:.6rem;"
            "padding:1rem;margin:.8rem 0}@media(prefers-color-scheme:dark){.card{background:#111a2e;border-color:#1e293b}}"
            ".chart{width:100%;height:360px}.tbl{width:100%;border-collapse:collapse;font-size:.85rem}"
            ".tbl th,.tbl td{border-bottom:1px solid #e2e8f0;padding:.35rem .5rem;text-align:left}"
            ".num{font-variant-numeric:tabular-nums}.flag{color:#b45309;margin:.3rem 0}"
            ".tabhdr{margin-top:1.4rem}.topbar{background:linear-gradient(100deg,#1e4fd6,#2f6df6);"
            "color:#fff;padding:.8rem 1.5rem;font-weight:600}"
        )
        return (
            "<!doctype html><html lang=zh><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Clousight Bench 指北测评</title><style>{css}</style></head><body>"
            "<div class=topbar>Clousight Bench · 报告</div><div class=wrap id=app></div>"
            f"<script>{_echarts_js()}</script>"
            f"<script>window.__BUNDLE__={data};</script>"
            f"<script>{_APP_JS}</script>"
            "</body></html>"
        )
```

Register it in `registry.py` `load_report_renderers`:

```python
    from clousight_bench.core.reporting.renderers.echarts import EchartsRenderer
    from clousight_bench.core.reporting.renderers.html import HtmlRenderer

    renderers: dict[str, ReportRenderer] = {"html": HtmlRenderer(), "echarts": EchartsRenderer()}
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_report_echarts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/clousight_bench/core/reporting/renderers/echarts.py src/clousight_bench/core/registry.py tests/test_report_echarts.py
git commit -m "feat(report): EchartsRenderer — self-contained ECharts report"
```

---

## Task 7: Make echarts the default + real-data smoke

**Files:**
- Modify: `src/clousight_bench/cli.py` (report argparse defaults)
- Test: `tests/test_cli_report_html.py`

**Interfaces:**
- `csbench report` default `--format html`, default `--renderer echarts`; `--renderer html` still works.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_report_html.py
def test_report_default_renderer_is_echarts(tmp_path, capsys):
    # (reuse this module's existing helper that writes a result record into tmp_path)
    ...  # write >=1 valid record + its flat T*.series.parquet into tmp_path
    from clousight_bench.cli import main
    rc = main(["report", "--results", str(tmp_path), "--format", "html",
               "--out", str(tmp_path / "r.html")])
    assert rc == 0
    html = (tmp_path / "r.html").read_text(encoding="utf-8")
    assert "window.__BUNDLE__" in html   # echarts renderer, not the SVG one
```

(Follow the record-writing pattern already used by `tests/test_cli_report_html.py`; if none, use `ResultStore` from `tests/test_store.py`'s `_rec` + `persist`.)

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/pytest tests/test_cli_report_html.py -k default_renderer -v`
Expected: FAIL (default renderer is still `html` → no `__BUNDLE__`).

- [ ] **Step 3: Flip the defaults**

In `cli.py` report parser:

```python
    rep_p.add_argument("--format", choices=["markdown", "html"], default="html")
    rep_p.add_argument("--renderer", default="echarts", help="report renderer name (default: echarts)")
```

- [ ] **Step 4: Run to verify pass + full regression**

Run: `.venv/bin/pytest tests/ -q`
Expected: all pass (fix any test that hard-coded the old default by asserting on `--renderer html` explicitly).

- [ ] **Step 5: Real-data smoke + regenerate the delivery artifact**

Run:
```bash
NO_PROXY='*' .venv/bin/csbench report --results results/prod-camp-27f20a62 \
  --renderer echarts --out results/prod-camp-27f20a62/report.html 2>&1 | tail
```
Expected: `wrote ...report.html`, and **NO** `skipped ... series` lines (all 27 tasks load now). Open the HTML; confirm quadrant + cold→warm line render.

- [ ] **Step 6: Commit**

```bash
git add src/clousight_bench/cli.py tests/test_cli_report_html.py
git commit -m "feat(report): default to the echarts renderer"
```

---

## Self-Review

- **Spec coverage:** sidecar fix → T1; chart kinds + splits + mode + embedded series → T2; series load → T3; quadrant/timeseries/stacked_bar panels → T4; vendored asset → T5; renderer + registry → T6; default flip + smoke → T7. All spec sections mapped.
- **Placeholders:** the only `...` are in Task 7's test where it reuses an existing module helper (record-writing) — the step names the exact source to copy. No code placeholders elsewhere.
- **Type consistency:** `ChartSpec(x_split, y_split)`, `DomainReport(mode, series)`, `build_bundle(series_by_task=)`, `_load_series -> {task:{series:[{t,value,unit}]}}`, `_quadrant_panel`, `build_timeseries_panels`, `EchartsRenderer.name="echarts"` — consistent across tasks.
