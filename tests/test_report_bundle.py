from clousight_bench.core.reporting.bundle import build_bundle
from clousight_bench.core.reporting.profiles import PROFILES


def test_two_platforms_same_execution_compare(report_record):
    recs = [
        report_record("aliyun-agentrun", "T1.1", execution="simulated",
                      measurements={"cold_start_ms": 900.0, "warm_start_p50_ms": 40.0}),
        report_record("aws-agentcore", "T1.1", execution="simulated",
                      measurements={"cold_start_ms": 700.0, "warm_start_p50_ms": 30.0}),
    ]
    b = build_bundle(recs, results_dir="r", generated_at="t", profiles=PROFILES)
    dom = b.domains[0]
    assert set(dom.platforms) == {"aliyun-agentrun", "aws-agentcore"}
    latency = [p for p in dom.panels if p.key == "latency"][0]
    assert latency.comparison is True
    assert {c.platform for c in latency.cells} == {"aliyun-agentrun", "aws-agentcore"}


def test_simulated_and_live_do_not_compare(report_record):
    recs = [
        report_record("aliyun-agentrun", "T1.1", execution="simulated",
                      measurements={"cold_start_ms": 900.0}),
        report_record("aliyun-agentrun", "T1.1", execution="live",
                      measurements={"cold_start_ms": 950.0}),
    ]
    b = build_bundle(recs, results_dir="r", generated_at="t", profiles=PROFILES)
    dom = b.domains[0]
    assert any("simulated" in f.lower() for f in dom.red_flags)
    for p in [p for p in dom.panels if p.key == "latency"]:
        assert len({c.execution for c in p.cells}) == 1


def test_bundle_is_json_serializable(report_record):
    import json
    b = build_bundle([report_record("a", "T1.1", measurements={"cold_start_ms": 1.0})],
                     results_dir="r", generated_at="t", profiles=PROFILES)
    assert json.loads(json.dumps(b.to_dict()))["schema"] == "report-bundle/1.0"
