from clousight_bench.core.reporting.bundle import build_bundle
from clousight_bench.core.reporting.profiles import PROFILES
from clousight_bench.core.reporting.renderers.html import HtmlRenderer


def test_html_is_self_contained_and_marks_execution(report_record):
    recs = [report_record("aliyun-agentrun", "T1.1", execution="simulated",
                          measurements={"cold_start_ms": 900.0})]
    b = build_bundle(recs, results_dir="r", generated_at="t", profiles=PROFILES)
    html = HtmlRenderer().render(b)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "aliyun-agentrun" in html and "simulated" in html
    assert "<svg" in html
    assert 'src="http' not in html


def test_css_is_appended(report_record):
    b = build_bundle([report_record("a", "T1.1", measurements={"cold_start_ms": 1.0})],
                     results_dir="r", generated_at="t", profiles=PROFILES)
    html = HtmlRenderer().render(b, css=".x{color:red}")
    assert ".x{color:red}" in html
