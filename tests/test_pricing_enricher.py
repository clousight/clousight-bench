"""The reference cost enricher prices 0.2 usage measurements into extensions."""

import pytest

from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    ResultRecord,
    RunInfo,
)
from clousight_bench.enrichers.pricing import PricingEnricher


def _record(adapter="local-sim", region="", measurements=None, extensions=None):
    return ResultRecord(
        run=RunInfo(
            run_id="r",
            started_at="2026-07-25T00:00:00Z",
            finished_at="2026-07-25T00:00:01Z",
            stages={"EXECUTE": "ok"},
        ),
        identity=Identity(
            domain="agent-runtime",
            task_id="T5.1",
            task_revision="1",
            scorer_revision="1",
            adapter=adapter,
            adapter_status="reference",
            core_version="0.2.0",
        ),
        environment=Environment(region=region, mode="local", python_version="3.12.0", os_name="Linux"),
        fingerprints=Fingerprints(benchmark="sha256:a", environment="sha256:b", implementation="sha256:c"),
        status="completed",
        measurements=measurements or {},
        extensions=extensions or {},
    )


def _usage(**units):
    return {u: {"value": v, "unit": u, "evidence": "B"} for u, v in units.items()}


def test_cost_computed_from_vcpu_hours():
    rec = _record("aws", "us-east-1", _usage(vcpu_hours=10))
    out = PricingEnricher().enrich(rec)
    pricing = out.extensions["pricing"]
    assert pricing["cost_usd"] == round(10 * 0.0895, 9)
    assert pricing["list_cost_usd"] == round(10 * 0.0895, 9)
    assert pricing["breakdown"][0]["unit"] == "vcpu_hours"
    assert pricing["breakdown"][0]["list_unit_price"] == 0.0895


def test_uncovered_usage_listed_but_does_not_crash():
    rec = _record("unknown-cloud", "", _usage(vcpu_hours=5))
    out = PricingEnricher().enrich(rec)
    assert out.extensions["pricing"]["cost_usd"] == 0.0
    assert "vcpu_hours" in out.extensions["pricing"]["uncovered"]


def test_no_usage_leaves_record_untouched():
    rec = _record("local-sim", "", {"recovery_mode": {"value": "auto-retry", "evidence": "C"}})
    out = PricingEnricher().enrich(rec)
    assert "pricing" not in out.extensions


def test_existing_pricing_is_not_overwritten():
    rec = _record("aws", "us-east-1", _usage(vcpu_hours=10), extensions={"pricing": {"cost_usd": 999.0}})
    out = PricingEnricher().enrich(rec)
    assert out.extensions["pricing"]["cost_usd"] == 999.0


def test_non_numeric_qty_raises_clear_error():
    rec = _record("aws", "us-east-1", _usage(vcpu_hours="ten"))
    with pytest.raises(TypeError, match="must be a number"):
        PricingEnricher().enrich(rec)


def test_bool_qty_rejected():
    rec = _record("aws", "us-east-1", _usage(vcpu_hours=True))
    with pytest.raises(TypeError, match="must be a number"):
        PricingEnricher().enrich(rec)


def test_cost_includes_invocations():
    rec = _record("aliyun", "cn-hangzhou", _usage(invocations=1_000_000))
    out = PricingEnricher().enrich(rec)
    assert out.extensions["pricing"]["cost_usd"] == round(1_000_000 * 0.0000003, 9)
    assert "invocations" in {b["unit"] for b in out.extensions["pricing"]["breakdown"]}


def test_data_override_via_env(tmp_path, monkeypatch):
    feed = tmp_path / "feed.json"
    feed.write_text(
        '{"prices": [{"provider": "aws", "service": "agent-runtime", '
        '"unit": "vcpu_hours", "region": "us-east-1", "price": 1.0, "source": "test"}]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CLOUSIGHT_PRICING_DATA", str(feed))
    rec = _record("aws", "us-east-1", _usage(vcpu_hours=3))
    out = PricingEnricher().enrich(rec)
    assert out.extensions["pricing"]["cost_usd"] == 3.0


def test_enricher_name():
    assert PricingEnricher().name == "pricing"


def _write_discounts(tmp_path, payload):
    import json

    p = tmp_path / "discounts.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def test_list_discount_net(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "CLOUSIGHT_PRICING_DISCOUNTS",
        _write_discounts(
            tmp_path,
            {"default_pct": 0, "discounts": [{"provider": "aliyun", "service": "agent-runtime", "pct": 30}]},
        ),
    )
    rec = _record(adapter="aliyun-agentrun", region="cn-hangzhou", measurements=_usage(invocations=8))
    out = PricingEnricher().enrich(rec).extensions["pricing"]
    assert out["list_cost_usd"] == round(8 * 0.0000003, 9)
    assert out["cost_usd"] == round(8 * 0.0000003 * 0.7, 9)
    assert out["discount_usd"] == round(out["list_cost_usd"] - out["cost_usd"], 9)
    b = out["breakdown"][0]
    assert b["discount_pct"] == 30 and b["list_unit_price"] == 0.0000003


def test_no_discount_source_net_equals_list(monkeypatch):
    monkeypatch.delenv("CLOUSIGHT_PRICING_DISCOUNTS", raising=False)
    rec = _record(adapter="aliyun-agentrun", region="cn-hangzhou", measurements=_usage(invocations=8))
    out = PricingEnricher().enrich(rec).extensions["pricing"]
    assert out["cost_usd"] == out["list_cost_usd"] and out["discount_usd"] == 0.0


def test_small_cost_not_rounded_to_zero(monkeypatch):
    monkeypatch.delenv("CLOUSIGHT_PRICING_DISCOUNTS", raising=False)
    rec = _record(adapter="aliyun-agentrun", region="cn-hangzhou", measurements=_usage(vcpu_hours=2e-6))
    out = PricingEnricher().enrich(rec).extensions["pricing"]
    assert out["cost_usd"] > 0.0


def test_provider_service_discount_beats_provider(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "CLOUSIGHT_PRICING_DISCOUNTS",
        _write_discounts(
            tmp_path,
            {
                "default_pct": 5,
                "discounts": [
                    {"provider": "aliyun", "pct": 10},
                    {"provider": "aliyun", "service": "agent-runtime", "pct": 40},
                ],
            },
        ),
    )
    rec = _record(adapter="aliyun-agentrun", region="cn-hangzhou", measurements=_usage(invocations=8))
    out = PricingEnricher().enrich(rec).extensions["pricing"]
    assert out["breakdown"][0]["discount_pct"] == 40
