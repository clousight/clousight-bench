import re

from clousight_bench.core.reporting.bundle import build_bundle
from clousight_bench.core.reporting.profiles import PROFILES
from clousight_bench.core.reporting.renderers.echarts import EchartsRenderer, _i18n_payload


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
    return build_bundle([rec], results_dir="r", generated_at="t", profiles=PROFILES, series_by_task=series)


def test_render_is_self_contained(report_record):
    html = EchartsRenderer().render(_bundle(report_record))
    assert "window.__BUNDLE__" in html
    assert "window.__I18N__" in html
    assert "echarts" in html.lower()
    assert len(html) > 900_000  # ECharts inlined
    assert re.search(r"<(script|link)\b[^>]*\b(src|href)\s*=\s*['\"]?https?:", html) is None
    assert "<script src" not in html and "//cdn" not in html


def test_render_is_bilingual_zh_default(report_record):
    html = EchartsRenderer().render(_bundle(report_record))
    assert "LANG = 'zh'" in html  # Chinese by default
    assert "冷启动(ms)" in html  # localized metric label embedded
    assert "证据等级" in html  # evidence legend in Chinese


def test_small_series_has_no_zoom():
    # Time-series of a handful of points must not offer dataZoom (infinite
    # zoom-out on 8 points is meaningless — the user called this out). Assert on
    # OUR app code, not the whole doc (the ECharts lib mentions dataZoom itself).
    from clousight_bench.core.reporting.renderers.echarts import _APP_JS

    assert "dataZoom" not in _APP_JS


def test_i18n_payload_covers_units_and_evidence():
    p = _i18n_payload()
    assert p["metric"]["cold_start_ms"]["unit"] == "ms"
    assert p["metric"]["availability"]["unit"] == "ratio"
    assert "ms" in p["unit"] and "ratio" in p["unit"]


def test_renderer_registered():
    from clousight_bench.core.registry import load_report_renderers

    assert "echarts" in load_report_renderers()
