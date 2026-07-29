import pytest

from clousight_bench.core.schema import ResultRecord, utc_now
from clousight_bench.enrichers.pricing import PricingEnricher


def _rec(platform, metrics):
    return ResultRecord(
        domain="agent-runtime", task_id="T5.1", platform=platform, run_id="r",
        started_at=utc_now(), finished_at=utc_now(),
        config_hash="sha256:x", evidence_layer="B", metrics=metrics,
    )


def test_cost_computed_from_vcpu_hours():
    rec = _rec("aws", {"vcpu_hours": 10, "service": "agent-runtime", "region": "us-east-1"})
    out = PricingEnricher().enrich(rec)
    assert out.metrics["cost_usd"] == round(10 * 0.0895, 6)
    breakdown = out.raw["pricing_breakdown"]
    assert breakdown[0]["unit"] == "vcpu_hours"
    assert breakdown[0]["qty"] == 10
    assert breakdown[0]["unit_price"] == 0.0895


def test_uncovered_usage_notes_but_does_not_crash():
    rec = _rec("unknown-cloud", {"vcpu_hours": 5, "service": "agent-runtime"})
    out = PricingEnricher().enrich(rec)
    assert out.metrics["cost_usd"] == 0.0
    assert "uncovered" in out.notes.lower()


def test_no_usage_leaves_record_untouched():
    # A wordcount smoke has no usage metrics -> the enricher must not annotate it.
    rec = _rec("local-sim", {"rows_processed": 100_000, "throughput_rows_per_s": 12345.6})
    out = PricingEnricher().enrich(rec)
    assert "cost_usd" not in out.metrics
    assert "pricing_breakdown" not in out.raw


def test_existing_cost_is_not_overwritten():
    # Transition guard: if another enricher already priced this, leave it alone.
    rec = _rec("aws", {"vcpu_hours": 10, "service": "agent-runtime",
                       "region": "us-east-1", "cost_usd": 999.0})
    out = PricingEnricher().enrich(rec)
    assert out.metrics["cost_usd"] == 999.0


def test_non_numeric_qty_raises_clear_error():
    rec = _rec("aws", {"vcpu_hours": "ten", "service": "agent-runtime", "region": "us-east-1"})
    with pytest.raises(TypeError, match="must be a number"):
        PricingEnricher().enrich(rec)


def test_bool_qty_rejected():
    rec = _rec("aws", {"vcpu_hours": True, "service": "agent-runtime", "region": "us-east-1"})
    with pytest.raises(TypeError, match="must be a number"):
        PricingEnricher().enrich(rec)


def test_cost_includes_invocations():
    rec = _rec("aliyun", {"invocations": 1_000_000, "service": "agent-runtime",
                          "region": "cn-hangzhou"})
    out = PricingEnricher().enrich(rec)
    assert out.metrics["cost_usd"] == round(1_000_000 * 0.0000003, 6)
    units = {b["unit"] for b in out.raw["pricing_breakdown"]}
    assert "invocations" in units


def test_data_override_via_env(tmp_path, monkeypatch):
    feed = tmp_path / "feed.json"
    feed.write_text(
        '{"prices": [{"provider": "aws", "service": "agent-runtime", '
        '"unit": "vcpu_hours", "region": "us-east-1", "price": 1.0, "source": "test"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CLOUSIGHT_PRICING_DATA", str(feed))
    rec = _rec("aws", {"vcpu_hours": 3, "service": "agent-runtime", "region": "us-east-1"})
    out = PricingEnricher().enrich(rec)
    assert out.metrics["cost_usd"] == 3.0


def test_enricher_name():
    assert PricingEnricher().name == "pricing"
