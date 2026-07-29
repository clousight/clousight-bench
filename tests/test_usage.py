import pytest

from clousight_bench.core.usage import USAGE_METRIC_KEYS, attach_usage


def test_attach_usage_records_known_keys():
    metrics = {}
    attach_usage(metrics, vcpu_hours=2.5, invocations=10)
    assert metrics == {"vcpu_hours": 2.5, "invocations": 10}


def test_attach_usage_skips_none():
    metrics = {}
    attach_usage(metrics, vcpu_hours=None, tokens_1k=3)
    assert metrics == {"tokens_1k": 3}


def test_attach_usage_rejects_unknown_key_without_mutating():
    metrics = {"vcpu_hours": 1}
    with pytest.raises(ValueError, match="unknown usage metric"):
        attach_usage(metrics, made_up_key=5, vcpu_hours=2)
    # validation happens before any write -> dict is untouched
    assert metrics == {"vcpu_hours": 1}


def test_all_known_keys_are_accepted():
    metrics = {}
    attach_usage(metrics, **{k: 1 for k in USAGE_METRIC_KEYS})
    assert set(metrics) == set(USAGE_METRIC_KEYS)
