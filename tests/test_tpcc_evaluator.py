"""The official-tpcc-evaluator (pure function over BenchBase summary.json)."""

from __future__ import annotations

import json
from pathlib import Path

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


def _artifacts(tmp_path: Path, summary: dict) -> RawArtifacts:
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    return RawArtifacts(dir=tmp_path, manifest={"summary": {"path": "summary.json", "rows": None}})


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
