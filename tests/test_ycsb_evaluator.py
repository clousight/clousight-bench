"""The official-ycsb-evaluator (pure function over RawArtifacts)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


# --- R5: reliability evidence from the tool's own Return= counts ---------------


def _out_with_errors() -> str:
    return (
        "[OVERALL], RunTime(ms), 1000\n"
        "[OVERALL], Throughput(ops/sec), 100.0\n"
        "[READ], Return=OK, 900\n"
        "[READ], Return=ERROR, 60\n"
        "[UPDATE], Return=OK, 30\n"
        "[UPDATE], Return=NOT_FOUND, 10\n"
    )


def test_error_rate_from_return_counts(tmp_path):
    out = OfficialYcsbEvaluator().evaluate(_artifacts(tmp_path, _out_with_errors()))
    assert out["ycsb.ops_ok"].value == 930
    assert out["ycsb.ops_failed"].value == 70  # every non-OK Return code counts
    assert out["ycsb.error_rate"].value == pytest.approx(70 / 1000)
    assert out["ycsb.error_rate"].unit == "ratio"
    assert out["ycsb.error_rate"].reproducibility_class == "environmental"
    assert "tool-reported" in out["ycsb.error_rate"].notes


def test_error_rate_zero_on_clean_run(tmp_path):
    clean = "[OVERALL], RunTime(ms), 10\n[READ], Return=OK, 100\n"
    out = OfficialYcsbEvaluator().evaluate(_artifacts(tmp_path, clean))
    assert out["ycsb.error_rate"].value == 0.0
    assert out["ycsb.ops_failed"].value == 0


def test_error_metrics_omitted_without_return_lines(tmp_path):
    out = OfficialYcsbEvaluator().evaluate(_artifacts(tmp_path, "[OVERALL], RunTime(ms), 5\n"))
    assert "ycsb.error_rate" not in out
    assert "ycsb.ops_ok" not in out


def test_completed_under_disruption_from_summary(tmp_path):
    (tmp_path / "ycsb_output.txt").write_text("[READ], Return=OK, 5\n[READ], Return=ERROR, 5\n")
    (tmp_path / "summary.json").write_text(
        json.dumps(
            {
                "disruption": {
                    "plan": {"action": "reset", "at_s": 2.0},
                    "fired": True,
                    "connections_reset": 1,
                    "disrupted_at_unix_nano": [1_757_000_000_000_000_000],
                }
            }
        )
    )
    raw = RawArtifacts(
        dir=tmp_path,
        manifest={
            "ycsb_output": {"path": "ycsb_output.txt", "rows": None},
            "summary": {"path": "summary.json", "rows": None},
        },
    )
    out = OfficialYcsbEvaluator().evaluate(raw)
    assert out["ycsb.completed_under_disruption"].value == 1.0
    assert "action=reset" in out["ycsb.completed_under_disruption"].notes
    assert out["ycsb.error_rate"].value == 0.5


def test_no_disruption_claim_without_summary_block(tmp_path):
    out = OfficialYcsbEvaluator().evaluate(_artifacts(tmp_path, "[READ], Return=OK, 5\n"))
    assert "ycsb.completed_under_disruption" not in out


def test_no_disruption_claim_when_the_plan_never_fired(tmp_path):
    """An armed plan whose run finished before at_s disrupted nothing —
    claiming completed_under_disruption would be a fabricated claim."""
    (tmp_path / "ycsb_output.txt").write_text("[READ], Return=OK, 5\n")
    (tmp_path / "summary.json").write_text(
        json.dumps(
            {
                "disruption": {
                    "plan": {"action": "reset", "at_s": 60.0},
                    "fired": False,
                    "connections_reset": 0,
                    "disrupted_at_unix_nano": [],
                }
            }
        )
    )
    raw = RawArtifacts(
        dir=tmp_path,
        manifest={
            "ycsb_output": {"path": "ycsb_output.txt", "rows": None},
            "summary": {"path": "summary.json", "rows": None},
        },
    )
    out = OfficialYcsbEvaluator().evaluate(raw)
    assert "ycsb.completed_under_disruption" not in out
