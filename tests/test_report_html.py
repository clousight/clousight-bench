from clousight_bench.core.reporting.bundle import build_bundle
from clousight_bench.core.reporting.profiles import PROFILES
from clousight_bench.core.reporting.renderers.html import HtmlRenderer


def _html(report_record, **kw):
    recs = [
        report_record("aliyun-agentrun", "T1.1", execution="simulated", measurements={"cold_start_ms": 900.0})
    ]
    return HtmlRenderer().render(
        build_bundle(recs, results_dir="r", generated_at="t", profiles=PROFILES), **kw
    )


def test_html_is_self_contained_and_marks_execution(report_record):
    html = _html(report_record)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "aliyun-agentrun" in html and "simulated" in html
    assert "<svg" in html
    assert 'src="http' not in html and 'href="http' not in html


def test_numbers_are_readable_not_scientific(report_record):
    from clousight_bench.core.reporting.renderers.html import _disp, _fmt

    assert _fmt(2.49e-06) == "0.00000249"  # never scientific
    assert _fmt(1506.24) == "1,506.24"  # thousands grouping
    assert _fmt(8.0) == "8" and _fmt(0.0) == "0"
    # a cost metric carries its currency from the pricing feed
    cost = {"name": "cost_usd", "value_num": 2.49e-06, "value_str": None, "unit": "USD"}
    assert _disp(cost) == "0.00000249&nbsp;USD"


def test_cost_shows_currency_from_feed(report_record):
    rec = report_record(
        "aliyun-agentrun",
        "T5.1",
        execution="simulated",
        extensions={
            "pricing": {
                "cost_usd": 0.0000025,
                "list_cost_usd": 0.0000025,
                "discount_usd": 0.0,
                "currency": "USD",
            }
        },
    )
    html = HtmlRenderer().render(build_bundle([rec], results_dir="r", generated_at="t", profiles=PROFILES))
    assert "0.0000025&nbsp;USD" in html
    assert "e-0" not in html  # no scientific notation anywhere


def test_css_is_appended(report_record):
    html = _html(report_record, css=".x{color:red}")
    assert ".x{color:red}" in html


def test_html_v3_shadcn_tabs_cards(report_record):
    html = _html(report_record)
    assert "lang='zh'" in html
    assert "data:image/png;base64," in html
    assert "hsl(217 71% 51%)" in html and "Inter" in html
    assert "prefers-color-scheme" in html
    # tabs + grouped comparison matrix + platform cards
    assert "class='tabbar'" in html and "data-tab=" in html
    assert "class='pcard'" in html and "class='ctable'" in html
    assert "group-header" in html
    assert "性能" in html and "Performance" in html  # tab i18n
    assert "冷启动" in html and "cold_start_ms" in html  # metric i18n
    assert "启动延迟" in html and "Startup latency" in html  # panel i18n
    assert "addEventListener" in html
    assert "云计算指北" in html and "Clousight Bench" in html
