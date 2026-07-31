from clousight_bench.core.reporting.bundle import ChartSpec
from clousight_bench.core.reporting.renderers import svg


def test_grouped_bar_svg_is_wellformed_and_contains_values():
    chart = ChartSpec("grouped_bar", "metric", "value",
                      [{"name": "aliyun", "points": [900.0, 40.0]},
                       {"name": "aws", "points": [700.0, 30.0]}])
    out = svg.grouped_bar_svg(chart, ["cold", "warm"])
    assert out.startswith("<svg") and out.rstrip().endswith("</svg>")
    assert "aliyun" in out and "900" in out
    assert "#1E3A8A" in out or "#10B981" in out  # brand palette
    assert "src=" not in out and 'href="http' not in out  # no external resources


def test_bar_and_line_svg():
    chart = ChartSpec("bar", "x", "y", [{"name": "a", "points": [5.0]}])
    assert svg.bar_svg(chart, ["x"]).startswith("<svg")
    line = ChartSpec("line", "x", "y", [{"name": "a", "points": [[1, 2], [2, 4]]}])
    assert "<polyline" in svg.line_svg(line)
