from clousight_bench.core.reporting.bundle import build_bundle
from clousight_bench.core.reporting.profiles import PROFILES


def test_cost_panel_has_three_dimensions(report_record):
    rec = report_record(
        "aliyun-agentrun",
        "T5.1",
        execution="simulated",
        extensions={"pricing": {"cost_usd": 0.7, "list_cost_usd": 1.0, "discount_usd": 0.3}},
    )
    b = build_bundle([rec], results_dir="r", generated_at="t", profiles=PROFILES)
    cost = [p for p in b.domains[0].panels if p.key == "cost"][0]
    names = {m["name"] for c in cost.cells for m in c.metrics}
    assert {"list_cost_usd", "discount_usd", "cost_usd"} <= names


def test_agent_runtime_groups_and_tabs(report_record):
    recs = [
        report_record("aliyun-agentrun", tid, execution="simulated", measurements={m: 1.0 for m in ms})
        for tid, ms in [
            ("T1.1", ["cold_start_ms", "cold_warm_ratio"]),
            ("T5.1", ["invocations"]),
            ("T1.3", ["total_attempts"]),
            ("T4.1", ["span_completeness"]),
        ]
    ]
    b = build_bundle(recs, results_dir="r", generated_at="t", profiles=PROFILES)
    panels = b.domains[0].panels
    tabs = {p.tab for p in panels}
    assert {"Performance", "Cost", "Reliability", "Observability"} <= tabs
    latency = [p for p in panels if p.key == "latency"][0]
    assert latency.tab == "Performance"
    assert latency.to_dict()["tab"] == "Performance"


def test_group_spanning_tasks_merges_platform_into_one_column(report_record):
    # Provisioning spans T0.1 + T0.2 — one platform must be a single column.
    recs = [
        report_record(
            "aliyun-agentrun", "T0.1", execution="simulated", measurements={"provision_ready_ms": 1500.0}
        ),
        report_record(
            "aliyun-agentrun",
            "T0.2",
            execution="simulated",
            measurements={"teardown_ms": 200.0, "residual_count": 0.0},
        ),
    ]
    b = build_bundle(recs, results_dir="r", generated_at="t", profiles=PROFILES)
    prov = [p for p in b.domains[0].panels if p.key == "provisioning"][0]
    assert [c.platform for c in prov.cells] == ["aliyun-agentrun"]
    names = [m["name"] for m in prov.cells[0].metrics]
    assert names == ["provision_ready_ms", "teardown_ms", "residual_count"]


def test_generic_profile_for_other_domain(report_record):
    rec = report_record(
        "local-process", "J1.1", domain="bigdata-emr", measurements={"throughput_ops": 1234.0}
    )
    b = build_bundle([rec], results_dir="r", generated_at="t", profiles=PROFILES)
    assert b.domains[0].profile == "generic"
    assert b.domains[0].panels


def test_quadrant_panel_points(report_record):
    from clousight_bench.core.reporting.profiles import PROFILES

    latest = {
        ("T1.13", "aliyun-agentrun", "live"): report_record(
            "aliyun-agentrun",
            "T1.13",
            execution="live",
            measurements={"cold_start_ms": 87000.0, "warm_start_p50_ms": 70.0},
        ),
        ("T1.1", "aliyun-agentrun", "live"): report_record(
            "aliyun-agentrun",
            "T1.1",
            execution="live",
            measurements={"cold_start_ms": 6000.0, "warm_start_p50_ms": 40.0},
        ),
    }
    panels = PROFILES["agent-runtime"].build_panels(latest)
    q = [p for p in panels if p.chart and p.chart.kind == "quadrant"][0]
    xs = sorted(pt["x"] for pt in q.chart.series)
    assert xs == [6000.0, 87000.0]
    assert q.chart.x_split == 46500.0


def test_timeseries_panels_from_series():
    from clousight_bench.core.reporting.profiles import build_timeseries_panels

    panels = build_timeseries_panels({"T1.13": {"curve_ms": [{"t": 1, "value": 1.0, "unit": ""}]}})
    assert len(panels) == 1
    assert panels[0].chart.kind == "timeseries"
    assert panels[0].task_ids == ["T1.13"]


def test_cost_panel_is_stacked_bar(report_record):
    from clousight_bench.core.reporting.profiles import PROFILES

    latest = {
        ("T5.1", "aliyun-agentrun", "live"): report_record(
            "aliyun-agentrun",
            "T5.1",
            execution="live",
            measurements={"list_cost_usd": 1.0, "discount_usd": 0.2, "cost_usd": 0.8},
        )
    }
    panels = PROFILES["agent-runtime"].build_panels(latest)
    cost = [p for p in panels if p.key == "cost"][0]
    assert cost.chart.kind == "stacked_bar"
