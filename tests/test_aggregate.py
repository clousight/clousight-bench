"""Tests for the R1 per-item substrate: ItemScore/ItemResult + core.aggregate."""

from __future__ import annotations

import math

import pytest

from clousight_bench.core.aggregate import (
    aggregate,
    aggregate_by_group,
    scored_values,
    status_counts,
)
from clousight_bench.core.observation import ItemResult, ItemScore, Measurement, ObservationError


def _item(item_id: str, metric: str, value, status: str = "ok", group: str = "") -> ItemResult:
    return ItemResult(
        item_id=item_id, group=group, scores=[ItemScore(metric=metric, value=value, status=status)]
    )


# --------------------------------------------------------------------------- types


def test_item_score_validates_status_and_metric() -> None:
    with pytest.raises(ObservationError):
        ItemScore(metric="", value=1)
    with pytest.raises(ObservationError):
        ItemScore(metric="m", value=1, status="bogus")


def test_item_result_requires_id() -> None:
    with pytest.raises(ObservationError):
        ItemResult(item_id="")


def test_item_result_to_dict_omits_empties_and_carries_scores() -> None:
    it = ItemResult(
        item_id="q1",
        group="algebra",
        output="42",
        scores=[ItemScore(metric="acc", value=True, reason="matched")],
    )
    d = it.to_dict()
    assert d["item_id"] == "q1"
    assert d["group"] == "algebra"
    assert d["output"] == "42"
    assert "input" not in d and "reference" not in d  # None omitted
    assert d["scores"][0] == {"metric": "acc", "value": True, "status": "ok", "reason": "matched"}


def test_measurement_ci_roundtrips_and_validates() -> None:
    m = Measurement(value=0.5, unit="ratio", ci=(0.3, 0.7))
    assert m.to_dict()["ci"] == [0.3, 0.7]
    assert Measurement(value=0.5, unit="ratio").to_dict().get("ci") is None
    with pytest.raises(ObservationError):
        Measurement(value=0.5, unit="ratio", ci=(0.8, 0.2))  # lo > hi


# ----------------------------------------------------------------------- aggregate


def test_scored_values_excludes_skip_and_error() -> None:
    items = [
        _item("a", "m", 1.0, "ok"),
        _item("b", "m", 0.0, "fail"),
        _item("c", "m", 1.0, "skip"),
        _item("d", "m", 1.0, "error"),
    ]
    assert scored_values(items, "m") == [1.0, 0.0]  # skip + error excluded


def test_status_counts() -> None:
    items = [_item("a", "m", 1, "ok"), _item("b", "m", 0, "fail"), _item("c", "m", 1, "skip")]
    assert status_counts(items, "m") == {"ok": 1, "fail": 1, "skip": 1, "error": 0}


def test_aggregate_ratio_binary_uses_wilson_ci() -> None:
    # 3 of 4 pass → 0.75, Wilson CI within (0,1) and straddling 0.75
    items = [_item(str(i), "acc", v) for i, v in enumerate([1, 1, 1, 0])]
    m = aggregate(items, "acc", "ratio")
    assert m is not None
    assert m.value == 0.75
    assert m.sample_count == 4
    assert m.aggregation == "ratio"
    assert m.ci is not None and 0.0 < m.ci[0] < 0.75 < m.ci[1] < 1.0


def test_aggregate_mean_partial_credit_normal_ci() -> None:
    items = [_item(str(i), "score", v) for i, v in enumerate([0.2, 0.4, 0.6, 0.8])]
    m = aggregate(items, "score", "mean")
    assert m is not None
    assert m.value == pytest.approx(0.5)
    assert m.ci is not None and m.ci[0] < 0.5 < m.ci[1]  # non-binary → normal CI


def test_aggregate_returns_none_when_nothing_scored() -> None:
    items = [_item("a", "m", 1.0, "skip")]
    assert aggregate(items, "m", "ratio") is None
    assert aggregate([], "m", "ratio") is None


def test_aggregate_geomean_and_percentile() -> None:
    items = [_item(str(i), "lat", v) for i, v in enumerate([1.0, 10.0, 100.0])]
    g = aggregate(items, "lat", "geomean", unit="ms", reproducibility_class="environmental")
    assert g is not None and g.value == pytest.approx(10.0)  # geomean(1,10,100)=10
    p = aggregate(items, "lat", "p50", unit="ms", reproducibility_class="environmental")
    assert p is not None and p.value == pytest.approx(10.0)


def test_aggregate_geomean_guards_nonpositive() -> None:
    items = [_item("a", "x", 0.0), _item("b", "x", 5.0)]
    m = aggregate(items, "x", "geomean")
    assert m is not None and m.value == 0.0  # no NaN/exception on a zero value


def test_aggregate_unknown_how_raises() -> None:
    with pytest.raises(ValueError, match="unknown aggregation"):
        aggregate([_item("a", "m", 1.0)], "m", "bogus")


def test_aggregate_by_group() -> None:
    items = [
        _item("a", "acc", 1, group="algebra"),
        _item("b", "acc", 0, group="algebra"),
        _item("c", "acc", 1, group="geometry"),
        _item("d", "acc", 1),  # no group → excluded
    ]
    by = aggregate_by_group(items, "acc", "ratio")
    assert set(by) == {"algebra", "geometry"}
    assert by["algebra"].value == 0.5
    assert by["geometry"].value == 1.0


def test_aggregation_reconciles_with_measurement() -> None:
    """The core R1 invariant: a Measurement equals the mean of its item scores."""
    items = [_item(str(i), "acc", v) for i, v in enumerate([1, 0, 1, 1, 0])]
    m = aggregate(items, "acc", "ratio")
    assert m is not None
    assert m.value == pytest.approx(sum(s.value for it in items for s in it.scores) / len(items))
    assert not math.isnan(m.value)
