from clousight_bench.core.reporting.bundle import build_bundle
from clousight_bench.core.reporting.profiles import PROFILES


def test_two_platforms_same_execution_compare(report_record):
    recs = [
        report_record(
            "aliyun-agentrun",
            "T1.1",
            execution="simulated",
            measurements={"cold_start_ms": 900.0, "warm_start_p50_ms": 40.0},
        ),
        report_record(
            "aws-agentcore",
            "T1.1",
            execution="simulated",
            measurements={"cold_start_ms": 700.0, "warm_start_p50_ms": 30.0},
        ),
    ]
    b = build_bundle(recs, results_dir="r", generated_at="t", profiles=PROFILES)
    dom = b.domains[0]
    assert set(dom.platforms) == {"aliyun-agentrun", "aws-agentcore"}
    latency = [p for p in dom.panels if p.key == "latency"][0]
    assert latency.comparison is True
    assert {c.platform for c in latency.cells} == {"aliyun-agentrun", "aws-agentcore"}


def test_simulated_and_live_do_not_compare(report_record):
    recs = [
        report_record(
            "aliyun-agentrun", "T1.1", execution="simulated", measurements={"cold_start_ms": 900.0}
        ),
        report_record("aliyun-agentrun", "T1.1", execution="live", measurements={"cold_start_ms": 950.0}),
    ]
    b = build_bundle(recs, results_dir="r", generated_at="t", profiles=PROFILES)
    dom = b.domains[0]
    assert any("simulated" in f.lower() for f in dom.red_flags)
    for p in [p for p in dom.panels if p.key == "latency"]:
        assert len({c.execution for c in p.cells}) == 1


def test_bundle_is_json_serializable(report_record):
    import json

    b = build_bundle(
        [report_record("a", "T1.1", measurements={"cold_start_ms": 1.0})],
        results_dir="r",
        generated_at="t",
        profiles=PROFILES,
    )
    assert json.loads(json.dumps(b.to_dict()))["schema"] == "report-bundle/1.1"


def test_bundle_mode_and_series(report_record):
    from clousight_bench.core.reporting.bundle import BUNDLE_SCHEMA, build_bundle
    from clousight_bench.core.reporting.profiles import PROFILES

    assert BUNDLE_SCHEMA == "report-bundle/1.1"
    rec = report_record(
        "aliyun-agentrun", "T1.13", execution="live", measurements={"cold_start_ms": 87000.0}
    )
    series = {
        "T1.13": {
            "curve_ms": [
                {"t": 1, "value": 87000.0, "unit": ""},
                {"t": 2, "value": 70.0, "unit": ""},
            ]
        }
    }
    b = build_bundle(
        [rec], results_dir="r", generated_at="t", profiles=PROFILES, series_by_task=series
    )
    dom = b.domains[0]
    assert dom.mode == "single"
    assert dom.series["T1.13"]["curve_ms"][0]["value"] == 87000.0
    d = b.to_dict()
    assert d["domains"][0]["mode"] == "single"
    assert d["domains"][0]["series"]["T1.13"]["curve_ms"][1]["value"] == 70.0


def test_chartspec_split_serialized():
    from clousight_bench.core.reporting.bundle import ChartSpec

    c = ChartSpec(
        kind="quadrant",
        x_label="x",
        y_label="y",
        series=[{"name": "p", "x": 1.0, "y": 2.0, "meta": {}}],
        x_split=1.0,
        y_split=2.0,
    )
    assert c.to_dict()["x_split"] == 1.0 and c.to_dict()["y_split"] == 2.0
