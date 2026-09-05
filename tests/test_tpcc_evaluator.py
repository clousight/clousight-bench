"""The official-tpcc-evaluator (pure function over BenchBase summary.json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clousight_bench.core.registry import load_evaluators
from clousight_bench.core.suite import RawArtifacts
from clousight_bench.suites.tpc_c.evaluator import OfficialTpccEvaluator

_SUMMARY = {
    "Throughput (requests/second)": 753.85,
    "Goodput (requests/second)": 741.02,
    "Latency Distribution": {
        "Average Latency (microseconds)": 1324.6,
        "Median Latency (microseconds)": 1020,
        "99th Percentile Latency (microseconds)": 5870,
    },
}


def _artifacts(tmp_path: Path, summary: dict, meta: dict | None = None) -> RawArtifacts:
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    manifest = {"summary": {"path": "summary.json", "rows": None}}
    if meta is not None:
        (tmp_path / "meta.json").write_text(json.dumps(meta))
        manifest["meta"] = {"path": "meta.json", "rows": None}
    return RawArtifacts(dir=tmp_path, manifest=manifest)


def test_registered_via_entry_point() -> None:
    evs = {e.evaluator_id: e for e in load_evaluators()}
    assert "official-tpcc-evaluator" in evs
    assert isinstance(evs["official-tpcc-evaluator"], OfficialTpccEvaluator)


def test_supports_only_tpcc() -> None:
    ev = OfficialTpccEvaluator()
    assert ev.supports("tpc-c", "benchbase-local")
    assert not ev.supports("ycsb", "benchbase-local")


def test_parses_throughput_goodput_and_latency(tmp_path: Path) -> None:
    out = OfficialTpccEvaluator().evaluate(_artifacts(tmp_path, _SUMMARY))
    assert out["tpc-c.throughput_req_per_sec"].value == 753.85
    assert out["tpc-c.goodput_req_per_sec"].value == 741.02
    assert out["tpc-c.p99_latency_us"].value == 5870.0
    assert out["tpc-c.median_latency_us"].value == 1020.0
    assert out["tpc-c.avg_latency_us"].value == 1324.6
    for m in out.values():
        assert m.reproducibility_class == "environmental"
        assert m.official is True


def test_absent_keys_are_omitted(tmp_path: Path) -> None:
    out = OfficialTpccEvaluator().evaluate(_artifacts(tmp_path, {"Throughput (requests/second)": 500}))
    assert out["tpc-c.throughput_req_per_sec"].value == 500.0
    assert "tpc-c.goodput_req_per_sec" not in out
    assert "tpc-c.p99_latency_us" not in out  # no Latency Distribution → omitted


def test_missing_or_broken_summary(tmp_path: Path) -> None:
    # missing file → {} not a raise
    raw = RawArtifacts(dir=tmp_path, manifest={"summary": {"path": "nope.json", "rows": None}})
    assert OfficialTpccEvaluator().evaluate(raw) == {}
    # non-dict json → {}
    (tmp_path / "summary.json").write_text("[]")
    assert OfficialTpccEvaluator().evaluate(_artifacts(tmp_path, [])) == {}  # type: ignore[arg-type]


def test_evaluate_over_the_committed_mock_fixture() -> None:
    from clousight_bench.suites.tpc_c.suite import TpccSuite

    out = OfficialTpccEvaluator().evaluate(TpccSuite().mock_artifacts({}))
    assert out["tpc-c.throughput_req_per_sec"].value > 0
    assert "tpc-c.p99_latency_us" in out


def test_tpmc_estimate_from_goodput_and_configured_mix(tmp_path):
    summary = {
        "Throughput (requests/second)": 753.85,
        "Goodput (requests/second)": 741.02,
    }
    out = OfficialTpccEvaluator().evaluate(_artifacts(tmp_path, summary, meta={"neworder_weight_pct": 45}))
    m = out["tpc-c.tpmc_estimate"]
    assert m.value == pytest.approx(741.02 * 60 * 0.45)
    assert m.unit == "tpm"
    assert m.reproducibility_class == "environmental"
    assert "unaudited" in m.notes and "not a measured NewOrder rate" in m.notes


def test_tpmc_estimate_defaults_weight_when_meta_absent(tmp_path):
    summary = {"Goodput (requests/second)": 100.0}
    out = OfficialTpccEvaluator().evaluate(_artifacts(tmp_path, summary))
    assert out["tpc-c.tpmc_estimate"].value == pytest.approx(100.0 * 60 * 0.45)


def test_tpmc_estimate_omitted_without_goodput(tmp_path):
    out = OfficialTpccEvaluator().evaluate(_artifacts(tmp_path, {"Throughput (requests/second)": 10.0}))
    assert "tpc-c.tpmc_estimate" not in out


@pytest.mark.parametrize(
    "meta_text",
    ["not json", "[1, 2]", '{"neworder_weight_pct": "abc"}', '{"neworder_weight_pct": "inf"}'],
)
def test_tpmc_estimate_survives_malformed_meta(tmp_path, meta_text):
    (tmp_path / "summary.json").write_text(json.dumps({"Goodput (requests/second)": 100.0}))
    (tmp_path / "meta.json").write_text(meta_text)
    raw = RawArtifacts(
        dir=tmp_path,
        manifest={
            "summary": {"path": "summary.json", "rows": None},
            "meta": {"path": "meta.json", "rows": None},
        },
    )
    out = OfficialTpccEvaluator().evaluate(raw)
    assert out["tpc-c.tpmc_estimate"].value == pytest.approx(100.0 * 60 * 0.45)
