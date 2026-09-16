"""The measured-path retry policy: opt-in, validated, and digest-visible."""

from __future__ import annotations

import pytest

from clousight_bench.suites.llm_common import RetryPolicy


def test_absent_config_is_one_attempt() -> None:
    """The default must be indistinguishable from today's single-shot call."""
    for params in (None, {}, {"retry": None}):
        policy = RetryPolicy.from_params(params)
        assert policy.max_attempts == 1
        assert policy.enabled is False
        assert policy.canonical() is None


def test_explicit_policy_is_read() -> None:
    policy = RetryPolicy.from_params(
        {"retry": {"max_attempts": 4, "backoff_base_s": 0.5, "backoff_max_s": 2.0}}
    )
    assert (policy.max_attempts, policy.backoff_base_s, policy.backoff_max_s) == (4, 0.5, 2.0)
    assert policy.enabled is True


def test_partial_policy_keeps_defaults_for_the_rest() -> None:
    policy = RetryPolicy.from_params({"retry": {"max_attempts": 2}})
    assert policy.max_attempts == 2
    assert policy.backoff_base_s == 0.2
    assert policy.backoff_max_s == 5.0


@pytest.mark.parametrize("bad", [0, -1])
def test_max_attempts_below_one_is_an_error_not_a_clamp(bad: int) -> None:
    """A typo must fail the run, not silently disable what the user asked for."""
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy.from_params({"retry": {"max_attempts": bad}})


def test_backoff_is_exponential_and_capped() -> None:
    policy = RetryPolicy.from_params(
        {"retry": {"max_attempts": 9, "backoff_base_s": 1.0, "backoff_max_s": 4.0}}
    )
    # 1-based attempt: 1 -> base, 2 -> 2x, 3 -> 4x, then the cap holds
    assert [policy.backoff_for(n) for n in (1, 2, 3, 4, 5)] == [1.0, 2.0, 4.0, 4.0, 4.0]


def test_canonical_is_none_when_disabled_and_stable_when_enabled() -> None:
    """The clean-run digest must not move; an enabled policy must be reproducible."""
    assert RetryPolicy.from_params({"retry": {"max_attempts": 1}}).canonical() is None
    first = RetryPolicy.from_params({"retry": {"max_attempts": 3}}).canonical()
    second = RetryPolicy.from_params({"retry": {"max_attempts": 3}}).canonical()
    assert first == second
    assert first == {"backoff_base_s": 0.2, "backoff_max_s": 5.0, "max_attempts": 3}
