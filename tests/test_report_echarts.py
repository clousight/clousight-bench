import re

from clousight_bench.core.reporting.bundle import build_bundle
from clousight_bench.core.reporting.profiles import PROFILES
from clousight_bench.core.reporting.renderers.echarts import EchartsRenderer


def _bundle(report_record):
    rec = report_record(
        "aliyun-agentrun",
        "T1.13",
        execution="live",
        measurements={"cold_start_ms": 87000.0, "warm_start_p50_ms": 70.0},
    )
    series = {
        "T1.13": {
            "curve_ms": [
                {"t": 1, "value": 87000.0, "unit": ""},
                {"t": 2, "value": 70.0, "unit": ""},
            ]
        }
    }
    return build_bundle(
        [rec], results_dir="r", generated_at="t", profiles=PROFILES, series_by_task=series
    )


def test_render_is_self_contained(report_record):
    html = EchartsRenderer().render(_bundle(report_record))
    assert "window.__BUNDLE__" in html
    assert "echarts" in html.lower()
    assert len(html) > 900_000  # ECharts inlined
    # No external RESOURCE load: no <script src=...> / <link href=...> pointing at
    # a URL, and no CDN reference. (The vendored ECharts source legitimately
    # embeds license/xmlns URLs as strings — those trigger no network request.)
    assert re.search(r"<(script|link)\b[^>]*\b(src|href)\s*=\s*['\"]?https?:", html) is None
    assert "<script src" not in html and "//cdn" not in html


def test_render_has_chart_kinds(report_record):
    html = EchartsRenderer().render(_bundle(report_record))
    assert "quadrant" in html and "timeseries" in html


def test_renderer_registered():
    from clousight_bench.core.registry import load_report_renderers

    assert "echarts" in load_report_renderers()
