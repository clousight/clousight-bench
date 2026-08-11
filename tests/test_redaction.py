"""Secrets and machine identity must never reach a record or a fingerprint."""

from clousight_bench.core.redaction import (
    REDACTED,
    find_identity_leaks,
    identity_values,
    redact,
    scrub_identities,
    scrub_identity_text,
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


def test_scrub_identity_text_removes_embedded_machine_identities():
    text = scrub_identity_text(
        "/home/alice/results: permission denied on build-box",
        identities=("alice", "build-box"),
    )
    assert "alice" not in text
    assert "build-box" not in text
    assert "permission denied" in text
    assert REDACTED in text


def test_scrub_identity_text_leaves_a_clean_message_alone():
    assert scrub_identity_text("connection reset", identities=("alice",)) == ("connection reset")


def test_short_common_identity_is_scrubbed_only_as_a_whole_word():
    text = scrub_identity_text(
        "user dev failed while device stayed healthy",
        identities=("dev",),
    )
    assert text == f"user {REDACTED} failed while device stayed healthy"


def test_scrub_identities_walks_a_whole_payload():
    payload = {
        "errors": [{"message": "/home/alice/x failed"}],
        "count": 3,
        "host": "build-box",
    }
    clean = scrub_identities(payload, identities=("alice", "build-box"))

    assert "alice" not in clean["errors"][0]["message"]
    assert clean["host"] == REDACTED
    assert clean["count"] == 3
    assert payload["host"] == "build-box"  # input untouched


def test_scrub_identities_never_rewrites_dictionary_keys_or_collides():
    payload = {"alice": "first", REDACTED: "second", "nested": {"alice": "alice"}}
    clean = scrub_identities(payload, identities=("alice",))

    assert set(clean) == {"alice", REDACTED, "nested"}
    assert clean["alice"] == "first"
    assert clean[REDACTED] == "second"
    assert clean["nested"] == {"alice": REDACTED}


def test_scrub_identities_uses_this_machine_by_default():
    import getpass

    user = getpass.getuser()
    assert user not in scrub_identities({"m": f"/home/{user}/x"})["m"]
