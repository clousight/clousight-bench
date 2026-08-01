from pathlib import Path

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


# ---------------------------------------------------------------------------
# _load_aggregates tests (Task 4)
# ---------------------------------------------------------------------------
import json
from clousight_bench.cli import _load_aggregates


def _write_agg(path: Path, agg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(agg), encoding="utf-8")


def test_load_aggregates_empty_dir(tmp_path):
    assert _load_aggregates(tmp_path) == []


def test_load_aggregates_reads_agg_files(tmp_path):
    agg = {
        "kind": "run_plan_aggregate",
        "plan_id": "plan-20260801-120000-abc",
        "identity": {"domain": "agent-runtime", "task_id": "T1.1", "adapter": "local-sim"},
        "plan": {"repeat": 5, "warmup": 0},
        "comparable": True, "notes": [], "measurements": {},
        "runs": {}, "status_counts": {},
    }
    _write_agg(tmp_path / "aggregates" / "agent-runtime" / "local-sim" / "T1.1-plan-abc.json", agg)
    result = _load_aggregates(tmp_path)
    assert len(result) == 1
    assert result[0]["plan_id"] == "plan-20260801-120000-abc"


def test_load_aggregates_keeps_highest_n(tmp_path):
    base = {"kind": "run_plan_aggregate",
            "identity": {"domain": "agent-runtime", "task_id": "T1.1", "adapter": "local-sim"},
            "comparable": True, "notes": [], "measurements": {},
            "runs": {}, "status_counts": {}}
    agg3 = {**base, "plan_id": "plan-20260801-120000-aaa", "plan": {"repeat": 3, "warmup": 0}}
    agg5 = {**base, "plan_id": "plan-20260801-120001-bbb", "plan": {"repeat": 5, "warmup": 0}}
    d = tmp_path / "aggregates" / "agent-runtime" / "local-sim"
    _write_agg(d / "T1.1-plan-aaa.json", agg3)
    _write_agg(d / "T1.1-plan-bbb.json", agg5)
    result = _load_aggregates(tmp_path)
    assert len(result) == 1
    assert result[0]["plan"]["repeat"] == 5


def test_load_aggregates_ignores_non_agg_files(tmp_path):
    d = tmp_path / "aggregates" / "agent-runtime" / "local-sim"
    d.mkdir(parents=True)
    (d / "not_an_agg.json").write_text('{"kind": "result_record"}', encoding="utf-8")
    (d / "garbage.json").write_text("not json", encoding="utf-8")
    assert _load_aggregates(tmp_path) == []
