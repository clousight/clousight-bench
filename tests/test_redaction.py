"""Secrets and machine identity must never reach a record or a fingerprint."""
from clousight_bench.core.redaction import (
    REDACTED,
    find_identity_leaks,
    identity_values,
    redact,
)


def test_secret_named_keys_are_redacted_at_every_depth():
    payload = {
        "region": "cn-hangzhou",
        "access_key_id": "AKID",
        "nested": {"api_token": "t", "Password": "p", "plain": 1},
        "items": [{"client_secret": "s"}],
    }
    clean = redact(payload)
    assert clean["region"] == "cn-hangzhou"
    assert clean["access_key_id"] == REDACTED
    assert clean["nested"]["api_token"] == REDACTED
    assert clean["nested"]["Password"] == REDACTED
    assert clean["nested"]["plain"] == 1
    assert clean["items"][0]["client_secret"] == REDACTED


def test_redact_does_not_mutate_the_input():
    payload = {"token": "t"}
    redact(payload)
    assert payload == {"token": "t"}


def test_identity_values_are_non_empty_strings():
    values = identity_values()
    assert all(isinstance(v, str) and len(v) >= 3 for v in values)
    assert len(set(values)) == len(values)


def test_find_identity_leaks_reports_paths_for_exact_matches():
    leaks = find_identity_leaks(
        {"a": {"host": "build-box"}, "b": ["build-box", "other"]},
        identities=("build-box",),
    )
    assert leaks == ["$.a.host", "$.b[0]"]


def test_find_identity_leaks_ignores_substrings_and_clean_payloads():
    assert find_identity_leaks({"a": "build-box-2"}, identities=("build-box",)) == []
    assert find_identity_leaks({"a": 1, "b": None}, identities=("build-box",)) == []
