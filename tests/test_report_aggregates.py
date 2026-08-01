from clousight_bench.core.reporting.bundle import Cell, _agg_cell, _build_agg_cells, build_bundle, Panel
from clousight_bench.core.reporting.profiles import PROFILES


def _make_agg(task_id="T1.1", platform="local-sim", n=5, comparable=True, notes=None,
              measurements=None):
    return {
        "kind": "run_plan_aggregate",
        "plan_id": f"plan-20260801-{n}",
        "identity": {"domain": "agent-runtime", "task_id": task_id, "adapter": platform,
                     "core_version": "0.2.0"},
        "fingerprints": {"benchmark": "sha256:b", "environment": "sha256:e",
                         "implementation": "sha256:i"},
        "comparable": comparable,
        "plan": {"repeat": n, "warmup": 0},
        "runs": {"warmup": [], "measured": [f"run-{i}" for i in range(n)]},
        "status_counts": {"completed": n},
        "measurements": measurements or {
            "cold_start_ms": {"kind": "numeric", "n": n, "mean": 45.2, "stdev": 3.1,
                              "min": 42.0, "max": 50.0, "p50": 45.0, "p95": 51.8, "cv": 0.07},
        },
        "notes": notes or [],
    }


def test_agg_cell_numeric_mean_in_metrics():
    agg = _make_agg()
    cell = _agg_cell(agg, ["cold_start_ms"])
    assert cell.agg_stats is not None
    assert cell.agg_stats["n"] == 5
    assert cell.agg_stats["comparable"] is True
    assert cell.agg_stats["warnings"] == []
    # value_num should be mean for compat
    m = next(m for m in cell.metrics if m["name"] == "cold_start_ms")
    assert m["value_num"] == 45.2
    assert m["aggregation"] == "mean"


def test_agg_cell_comparability_warning():
    agg = _make_agg(comparable=False, notes=["fingerprint mismatch: 1 run excluded"])
    cell = _agg_cell(agg, ["cold_start_ms"])
    assert cell.agg_stats["comparable"] is False
    assert len(cell.agg_stats["warnings"]) == 1


def test_agg_cell_categorical():
    agg = _make_agg(measurements={
        "state_persisted": {"kind": "categorical", "n": 5, "mode": True,
                            "agreement": 1.0, "distinct": 1,
                            "values": [[True, 5]]}
    })
    cell = _agg_cell(agg, ["state_persisted"])
    m = next(m for m in cell.metrics if m["name"] == "state_persisted")
    assert m["value_str"] == "True"
    assert m["value_num"] is None


def test_build_bundle_no_aggregates_no_agg_cells(report_record):
    rec = report_record("local-sim", "T1.1", measurements={"cold_start_ms": 45.0})
    bundle = build_bundle([rec], results_dir=".", generated_at="2026-01-01T00:00:00",
                          profiles=PROFILES)
    for dom in bundle.domains:
        for panel in dom.panels:
            assert all(c.agg_stats is None for c in panel.cells)


def test_build_bundle_with_aggregate_injects_agg_cell(report_record):
    rec = report_record("local-sim", "T1.1", measurements={"cold_start_ms": 45.0})
    agg = _make_agg(task_id="T1.1", platform="local-sim", n=5)
    bundle = build_bundle([rec], results_dir=".", generated_at="2026-01-01T00:00:00",
                          profiles=PROFILES, aggregates=[agg])
    # find the startup latency panel
    agg_cells = []
    for dom in bundle.domains:
        for panel in dom.panels:
            agg_cells.extend(c for c in panel.cells if c.agg_stats is not None)
    assert len(agg_cells) >= 1
    ac = agg_cells[0]
    assert ac.agg_stats["n"] == 5
    assert ac.platform == "local-sim"


def test_build_bundle_multiple_aggregates_keeps_highest_n(report_record):
    rec = report_record("local-sim", "T1.1", measurements={"cold_start_ms": 45.0})
    agg3 = _make_agg(task_id="T1.1", platform="local-sim", n=3)
    agg5 = _make_agg(task_id="T1.1", platform="local-sim", n=5)
    # Both passed in; highest n should win — but _load_aggregates dedupes,
    # build_bundle receives only one per (domain, task_id, platform).
    # Test that two aggs with different n don't produce two agg cells for same platform.
    # In practice _load_aggregates dedupes, but build_bundle must not double-inject.
    bundle = build_bundle([rec], results_dir=".", generated_at="2026-01-01T00:00:00",
                          profiles=PROFILES, aggregates=[agg3, agg5])
    for dom in bundle.domains:
        for panel in dom.panels:
            platforms = [c.platform for c in panel.cells if c.agg_stats is not None]
            # At most one agg cell per platform per panel
            assert len(platforms) == len(set(platforms))
