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


def test_html_is_branded_and_bilingual(report_record):
    from clousight_bench.core.reporting.bundle import build_bundle
    from clousight_bench.core.reporting.profiles import PROFILES
    from clousight_bench.core.reporting.renderers.html import HtmlRenderer
    recs = [report_record("aliyun-agentrun", "T1.1", execution="simulated",
                          measurements={"cold_start_ms": 900.0})]
    html = HtmlRenderer().render(build_bundle(recs, results_dir="r", generated_at="t",
                                              profiles=PROFILES))
    assert "lang='zh'" in html
    assert "#1E3A8A" in html and "#10B981" in html
    assert "clousightCloudGradient" in html
    assert "Space Grotesk" in html and "DM Sans" in html
    assert "启动延迟" in html and "Startup latency" in html
    assert 'src="http' not in html and 'href="http' not in html
