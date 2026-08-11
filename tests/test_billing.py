"""The serverless billing model applies per-invocation minimum + rounding, so a
modeled cost tracks how the bill is actually computed."""

import pytest

from clousight_bench.core.billing import ComputeBillingRule, billed_compute
from clousight_bench.core.usage import USAGE_METRIC_KEYS, attach_usage


def test_default_rule_bills_exact_duration():
    rule = ComputeBillingRule()
    assert rule.billed_ms(3.0) == 3.0
    assert rule.billed_ms(3.4) == 4.0  # default 1 ms granularity rounds up


def test_minimum_duration_dominates_short_calls():
    rule = ComputeBillingRule(min_ms=100.0, rounding_ms=1.0)
    assert rule.billed_ms(3.0) == 100.0  # a 3 ms call still bills the 100 ms floor
    assert rule.billed_ms(250.4) == 251.0  # above the floor, rounded up


def test_rounding_granularity_rounds_up_per_invocation():
    rule = ComputeBillingRule(min_ms=0.0, rounding_ms=100.0)
    assert rule.billed_ms(1.0) == 100.0
    assert rule.billed_ms(150.0) == 200.0


def test_negative_duration_rejected():
    with pytest.raises(ValueError, match="must be >= 0"):
        ComputeBillingRule().billed_ms(-1.0)


def test_billed_compute_sums_per_invocation_then_scales():
    # Three 40 ms calls under a 100 ms floor -> 3 x 100 ms = 0.3 billed seconds.
    rule = ComputeBillingRule(min_ms=100.0)
    usage = billed_compute([40.0, 40.0, 40.0], vcpu=2.0, memory_gb=4.0, rule=rule)
    assert usage["requests"] == 3.0
    assert usage["vcpu_seconds"] == pytest.approx(0.3 * 2.0)  # 0.6
    assert usage["gb_seconds"] == pytest.approx(0.3 * 4.0)  # 1.2


def test_billed_compute_output_is_priceable_vocabulary():
    usage = billed_compute([10.0], vcpu=1.0, memory_gb=1.0)
    assert set(usage) <= set(USAGE_METRIC_KEYS)  # every key is a known usage unit
    metrics: dict = {}
    attach_usage(metrics, **usage)  # would raise on an unknown key
    assert metrics["requests"] == 1.0


def test_empty_plan_is_zero_cost_usage():
    usage = billed_compute([], vcpu=8.0, memory_gb=16.0)
    assert usage == {"requests": 0.0, "vcpu_seconds": 0.0, "gb_seconds": 0.0}
