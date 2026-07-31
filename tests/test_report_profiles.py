from clousight_bench.core.reporting.bundle import build_bundle
from clousight_bench.core.reporting.profiles import PROFILES


def test_cost_panel_has_three_dimensions(report_record):
    rec = report_record("aliyun-agentrun", "T5.1", execution="simulated",
                        extensions={"pricing": {"cost_usd": 0.7, "list_cost_usd": 1.0,
                                                "discount_usd": 0.3}})
    b = build_bundle([rec], results_dir="r", generated_at="t", profiles=PROFILES)
    cost = [p for p in b.domains[0].panels if p.key == "cost"][0]
    names = {m["name"] for c in cost.cells for m in c.metrics}
    assert {"list_cost_usd", "discount_usd", "cost_usd"} <= names


def test_generic_profile_for_other_domain(report_record):
    rec = report_record("local-process", "J1.1", domain="bigdata-emr",
                        measurements={"throughput_ops": 1234.0})
    b = build_bundle([rec], results_dir="r", generated_at="t", profiles=PROFILES)
    assert b.domains[0].profile == "generic"
    assert b.domains[0].panels
