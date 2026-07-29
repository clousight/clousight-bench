"""Statistical aggregation is pure, honest about small samples, and never
silently mixes a number with a label or one evidence layer with another."""
import pytest

from clousight_bench.core.statistics import (
    aggregate_measurements,
    is_numeric,
    summarize_categorical,
    summarize_numeric,
)


def test_bool_is_a_label_not_a_number():
    assert is_numeric(3) and is_numeric(2.5)
    assert not is_numeric(True) and not is_numeric(False)
    assert not is_numeric("x") and not is_numeric(None)


def test_numeric_summary_reports_spread_and_cv():
    summary = summarize_numeric([10.0, 12.0, 14.0])
    assert summary["kind"] == "numeric"
    assert summary["n"] == 3
    assert summary["mean"] == 12.0
    assert summary["min"] == 10.0 and summary["max"] == 14.0
    assert summary["p50"] == 12.0
    assert summary["stdev"] == pytest.approx(2.0)
    assert summary["cv"] == pytest.approx(2.0 / 12.0)


def test_single_sample_has_zero_stdev_and_the_value_as_every_percentile():
    summary = summarize_numeric([7.0])
    assert summary["n"] == 1
    assert summary["stdev"] == 0.0
    assert summary["p50"] == 7.0 and summary["p95"] == 7.0


def test_p95_is_nearest_rank_so_it_is_always_an_observed_value():
    summary = summarize_numeric([1.0, 2.0, 3.0, 4.0, 100.0])
    assert summary["p95"] == 100.0
    assert summary["max"] == 100.0


def test_cv_is_none_when_the_mean_is_zero():
    assert summarize_numeric([-1.0, 1.0])["cv"] is None


def test_categorical_summary_reports_the_distribution_and_agreement():
    summary = summarize_categorical(["a", "a", "b"])
    assert summary["kind"] == "categorical"
    assert summary["n"] == 3
    assert summary["distinct"] == 2
    assert summary["mode"] == "a"
    assert summary["agreement"] == pytest.approx(2 / 3)
    assert summary["values"] == [["a", 2], ["b", 1]]


def test_empty_samples_are_rejected_not_guessed():
    with pytest.raises(ValueError):
        summarize_numeric([])
    with pytest.raises(ValueError):
        summarize_categorical([])


def _m(value, unit="ms", evidence="C"):
    return {"value": value, "unit": unit, "evidence": evidence}


def test_aggregate_pools_a_measurement_across_records():
    out = aggregate_measurements(
        [{"lat": _m(10.0)}, {"lat": _m(20.0)}, {"lat": _m(30.0)}]
    )
    assert out["lat"]["kind"] == "numeric"
    assert out["lat"]["mean"] == 20.0
    assert out["lat"]["unit"] == "ms" and out["lat"]["evidence"] == "C"


def test_one_label_anywhere_makes_the_whole_measurement_categorical():
    out = aggregate_measurements([{"x": _m(10.0)}, {"x": _m("timeout")}])
    assert out["x"]["kind"] == "categorical"
    assert out["x"]["n"] == 2


def test_mixed_units_and_evidence_are_blanked_with_a_note():
    out = aggregate_measurements(
        [{"x": _m(1.0, unit="ms", evidence="C")},
         {"x": _m(2.0, unit="s", evidence="B")}]
    )
    assert out["x"]["unit"] == "" and out["x"]["evidence"] == ""
    assert "mixed units across repeats" in out["x"]["notes"]
    assert "mixed evidence layers across repeats" in out["x"]["notes"]


def test_a_measurement_absent_from_some_records_pools_only_where_present():
    out = aggregate_measurements([{"a": _m(1.0)}, {}, {"a": _m(3.0)}])
    assert out["a"]["n"] == 2
    assert out["a"]["mean"] == 2.0


def test_no_measurements_yields_no_aggregates():
    assert aggregate_measurements([{}, {}]) == {}
