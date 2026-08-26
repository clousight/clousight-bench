"""Completeness tests for the viewer's bilingual dictionaries (web/src/i18n/*.json).

Since the React build (viewer v3) the locale dictionaries are plain JSON files
under ``web/src/i18n/`` that Vite bundles into the app. The contract (mirroring
the deepseek-harness locale pattern): the ``en`` and ``zh`` key sets are checked
complete against each other — in BOTH directions — every value is a non-empty
string, and the key set covers the full Tasks 2-3 UI surface (>=54 keys). The
views must draw all UI strings from this key set instead of inventing hardcoded
literals (enforced by test_viewer_frontend.py's t('...') reference scan).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_I18N_DIR = Path(__file__).resolve().parents[1] / "web" / "src" / "i18n"


def _locale_dict(locale: str) -> dict[str, object]:
    path = _I18N_DIR / f"{locale}.json"
    assert path.is_file(), f"missing locale dictionary: {path}"
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), f"{path} must be a flat JSON object"
    return parsed


@pytest.fixture(scope="module")
def en() -> dict[str, object]:
    return _locale_dict("en")


@pytest.fixture(scope="module")
def zh() -> dict[str, object]:
    return _locale_dict("zh")


def test_key_sets_equal_both_ways(en: dict[str, object], zh: dict[str, object]) -> None:
    missing_in_zh = sorted(set(en) - set(zh))
    missing_in_en = sorted(set(zh) - set(en))
    assert not missing_in_zh, f"keys present in en but missing in zh: {missing_in_zh}"
    assert not missing_in_en, f"keys present in zh but missing in en: {missing_in_en}"


@pytest.mark.parametrize("locale", ["en", "zh"])
def test_values_are_nonempty_strings(locale: str) -> None:
    for key, value in _locale_dict(locale).items():
        assert isinstance(value, str), f"{locale}[{key!r}] is not a string: {value!r}"
        assert value.strip(), f"{locale}[{key!r}] is empty/blank"


def test_covers_full_key_set(en: dict[str, object]) -> None:
    assert len(en) >= 54, f"expected the full viewer key set (>=54 keys), got {len(en)}"


def test_keys_are_namespaced(en: dict[str, object]) -> None:
    """Every key is ``<namespace>.<name>`` from the agreed namespaces."""
    namespaces = {"header", "common", "list", "detail", "trace", "status"}
    for key in en:
        ns, _, name = key.partition(".")
        assert ns in namespaces and name, f"key {key!r} is not namespaced as expected"
