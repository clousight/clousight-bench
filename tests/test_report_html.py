from clousight_bench.core.reporting.bundle import build_bundle
from clousight_bench.core.reporting.profiles import PROFILES
from clousight_bench.core.reporting.renderers.html import HtmlRenderer


def _html(report_record, **kw):
    recs = [report_record("aliyun-agentrun", "T1.1", execution="simulated",
                          measurements={"cold_start_ms": 900.0})]
    return HtmlRenderer().render(build_bundle(recs, results_dir="r", generated_at="t",
                                              profiles=PROFILES), **kw)


def test_html_is_self_contained_and_marks_execution(report_record):
    html = _html(report_record)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "aliyun-agentrun" in html and "simulated" in html
    assert "<svg" in html
    assert 'src="http' not in html and 'href="http' not in html


def test_css_is_appended(report_record):
    html = _html(report_record, css=".x{color:red}")
    assert ".x{color:red}" in html


def test_html_v2_brand_i18n_provider_and_interaction(report_record):
    html = _html(report_record)
    assert "lang='zh'" in html
    assert "data:image/png;base64," in html
    assert "hsl(217 71% 51%)" in html and "Inter" in html
    assert "冷启动" in html and "cold_start_ms" in html
    assert "启动延迟" in html and "Startup latency" in html
    assert "addEventListener" in html
    assert "云计算指北" in html and "Clousight Bench" in html
