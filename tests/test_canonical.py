"""Canonical JSON: the single encoding every fingerprint and digest agrees on."""
import pytest

from clousight_bench.core.canonical import CanonicalJSONError, canonical_json, digest


def test_key_order_does_not_change_the_encoding():
    a = {"x": 1, "y": [1, 2], "z": {"b": 2, "a": 1}}
    b = {"z": {"a": 1, "b": 2}, "y": [1, 2], "x": 1}
    assert canonical_json(a) == canonical_json(b)
    assert canonical_json(a) == '{"x":1,"y":[1,2],"z":{"a":1,"b":2}}'


def test_no_insignificant_whitespace_and_unicode_is_not_escaped():
    assert canonical_json({"名字": "指北"}) == '{"名字":"指北"}'


def test_nan_and_infinity_are_rejected():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(CanonicalJSONError):
            canonical_json({"v": bad})


def test_negative_zero_is_normalised():
    assert canonical_json({"v": -0.0}) == canonical_json({"v": 0.0})


def test_tuples_encode_as_arrays():
    assert canonical_json({"v": (1, 2)}) == canonical_json({"v": [1, 2]})


def test_non_string_keys_and_unsupported_types_are_rejected():
    with pytest.raises(CanonicalJSONError):
        canonical_json({1: "a"})
    with pytest.raises(CanonicalJSONError):
        canonical_json({"v": {1, 2}})


def test_digest_is_full_sha256_and_content_sensitive():
    value = digest({"x": 1})
    assert value.startswith("sha256:")
    assert len(value) == len("sha256:") + 64
    assert all(c in "0123456789abcdef" for c in value.removeprefix("sha256:"))
    assert digest({"x": 1}) != digest({"x": 2})
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
