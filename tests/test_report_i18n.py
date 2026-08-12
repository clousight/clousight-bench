from clousight_bench.core.reporting.renderers import i18n


def test_t_emits_both_languages():
    out = i18n.t("Startup latency")
    assert "启动延迟" in out and "Startup latency" in out
    assert "class='zh'" in out and "class='en'" in out


def test_t_falls_back_to_english_when_untranslated():
    out = i18n.t("Totally Unknown Label")
    assert out.count("Totally Unknown Label") == 2


def test_full_ui_and_metric_translation():
    from clousight_bench.core.reporting.renderers import i18n

    for en in ("simulated", "state-persistence", "agent-runtime"):
        assert i18n.UI_STRINGS.get(en)
    m = i18n.tm("cold_start_ms")
    assert "冷启动" in m and "cold_start_ms" in m
