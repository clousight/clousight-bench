"""The official-ycsb-evaluator (pure function over RawArtifacts)."""

from __future__ import annotations

from pathlib import Path

from clousight_bench.core.registry import load_evaluators
from clousight_bench.core.suite import RawArtifacts
from clousight_bench.suites.ycsb.evaluator import OfficialYcsbEvaluator

_SAMPLE = (
    "[OVERALL], RunTime(ms), 1423\n"
    "[OVERALL], Throughput(ops/sec), 7027.4\n"
    "[READ], 99thPercentileLatency(us), 402\n"
    "[UPDATE], 99thPercentileLatency(us), 356\n"
)


def _artifacts(tmp_path: Path, text: str) -> RawArtifacts:
    (tmp_path / "ycsb_output.txt").write_text(text)
    return RawArtifacts(dir=tmp_path, manifest={"ycsb_output": {"path": "ycsb_output.txt", "rows": None}})


def test_registered_via_entry_point() -> None:
    evs = {e.evaluator_id: e for e in load_evaluators()}
    assert "official-ycsb-evaluator" in evs
    assert isinstance(evs["official-ycsb-evaluator"], OfficialYcsbEvaluator)


def test_supports_only_ycsb() -> None:
    ev = OfficialYcsbEvaluator()
    assert ev.supports("ycsb", "ycsb-local")
    assert not ev.supports("tpc-h", "ycsb-local")


def test_parses_all_metrics(tmp_path: Path) -> None:
    out = OfficialYcsbEvaluator().evaluate(_artifacts(tmp_path, _SAMPLE))
    assert out["ycsb.throughput_ops"].value == 7027.4
    assert out["ycsb.overall_runtime_ms"].value == 1423.0
    assert out["ycsb.read_p99_us"].value == 402.0
    assert out["ycsb.update_p99_us"].value == 356.0
    for m in out.values():
        assert m.reproducibility_class == "environmental"
        assert m.official is True


def test_absent_metric_is_omitted(tmp_path: Path) -> None:
    # A read-only workload (workloadc) has no UPDATE line → that metric is omitted.
    text = "[OVERALL], Throughput(ops/sec), 5000\n[READ], 99thPercentileLatency(us), 300\n"
    out = OfficialYcsbEvaluator().evaluate(_artifacts(tmp_path, text))
    assert "ycsb.update_p99_us" not in out
    assert "ycsb.throughput_ops" in out


def test_missing_or_empty_output(tmp_path: Path) -> None:
    assert OfficialYcsbEvaluator().evaluate(_artifacts(tmp_path, "")) == {}
    # missing file → {} not a raise
    raw = RawArtifacts(dir=tmp_path, manifest={"ycsb_output": {"path": "nope.txt", "rows": None}})
    assert OfficialYcsbEvaluator().evaluate(raw) == {}


def test_evaluate_over_the_committed_mock_fixture() -> None:
    from clousight_bench.suites.ycsb.suite import YcsbSuite

    out = OfficialYcsbEvaluator().evaluate(YcsbSuite().mock_artifacts({}))
    assert out["ycsb.throughput_ops"].value > 0
    assert "ycsb.read_p99_us" in out
