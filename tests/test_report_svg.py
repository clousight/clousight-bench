from clousight_bench.core.reporting.bundle import ChartSpec
from clousight_bench.core.reporting.renderers import svg
from clousight_bench.core.reporting.renderers.charts_js import CHART_JS


def test_grouped_bar_svg_is_wellformed_and_contains_values():
    chart = ChartSpec(
        "grouped_bar",
        "metric",
        "value",
        [{"name": "aliyun", "points": [900.0, 40.0]}, {"name": "aws", "points": [700.0, 30.0]}],
    )
    out = svg.grouped_bar_svg(chart, ["cold", "warm"])
    assert out.startswith("<svg") and out.rstrip().endswith("</svg>")
    assert "aliyun" in out and "900" in out
    assert "src=" not in out and 'href="http' not in out


def test_premium_svg_has_gridlines_ticks_and_data_hooks():
    chart = ChartSpec("grouped_bar", "metric", "value", [{"name": "aliyun", "points": [900.0, 40.0]}])
    out = svg.grouped_bar_svg(chart, ["cold", "warm"])
    assert "linearGradient" in out and "data-value=" in out
    assert "class='grid'" in out
    assert "hsl(217 71% 51%)" in out or "url(#barGrad)" in out


def test_line_svg_has_points_and_hooks():
    line = ChartSpec("line", "x", "y", [{"name": "a", "points": [[1, 2], [2, 4]]}])
    out = svg.line_svg(line)
    assert "<polyline" in out and "data-value=" in out


def test_charts_js_is_a_small_inline_script():
    assert "addEventListener" in CHART_JS and "tip" in CHART_JS
    assert "http" not in CHART_JS


def test_charts_js_has_tab_switch():
    assert "data-tab" in CHART_JS and "tabpanel" in CHART_JS
