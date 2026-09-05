"""The official-tpcds-evaluator (pure function over RawArtifacts)."""

from __future__ import annotations

import json
import math
from pathlib import Path

from clousight_bench.core.registry import load_evaluators
from clousight_bench.core.suite import RawArtifacts
from clousight_bench.suites.tpc_ds.evaluator import OfficialTpcdsEvaluator


def _artifacts(tmp_path: Path, queries: list[dict], summary: dict) -> RawArtifacts:
    (tmp_path / "queries.json").write_text(json.dumps(queries))
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    manifest = {
        "queries": {"path": "queries.json", "rows": len(queries)},
        "summary": {"path": "summary.json", "rows": None},
    }
    return RawArtifacts(dir=tmp_path, manifest=manifest)


def _ref_digest(nr: int) -> str:
    ref = json.loads(
        (
            Path(__file__).resolve().parent.parent
            / "src/clousight_bench/suites/tpc_ds/fixtures/reference/sf1_digests.json"
        ).read_text()
    )
    return ref[str(nr)]["result_digest"]


def test_registered_via_entry_point() -> None:
    evs = {e.evaluator_id: e for e in load_evaluators()}
    assert "official-tpcds-evaluator" in evs
    assert isinstance(evs["official-tpcds-evaluator"], OfficialTpcdsEvaluator)


def test_supports_only_tpcds() -> None:
    ev = OfficialTpcdsEvaluator()
    assert ev.supports("tpc-ds", "duckdb-local")
    assert not ev.supports("swe-bench", "duckdb-local")


def test_perf_metrics_and_correctness_at_sf1(tmp_path: Path) -> None:
    # Two queries matching the reference → queries_passed == 1.0.
    queries = [
        {"query_nr": 3, "latency_ms": 10.0, "row_count": 89, "result_digest": _ref_digest(3)},
        {"query_nr": 7, "latency_ms": 40.0, "row_count": 100, "result_digest": _ref_digest(7)},
    ]
    summary = {"scale_factor": 1.0, "query_count": 2}
    out = OfficialTpcdsEvaluator().evaluate(_artifacts(tmp_path, queries, summary))

    assert out["tpc-ds.queries_passed"].value == 1.0
    assert out["tpc-ds.queries_passed"].reproducibility_class == "deterministic"
    assert out["tpc-ds.queries_passed"].official is True
    assert out["tpc-ds.total_runtime_ms"].value == 50.0
    assert out["tpc-ds.total_runtime_ms"].reproducibility_class == "environmental"
    assert out["tpc-ds.total_runtime_ms"].official is True
    # geomean of 10 and 40 = sqrt(400) = 20
    assert math.isclose(out["tpc-ds.geomean_latency_ms"].value, 20.0, rel_tol=1e-9)


def test_partial_correctness(tmp_path: Path) -> None:
    queries = [
        {"query_nr": 3, "latency_ms": 5.0, "row_count": 89, "result_digest": _ref_digest(3)},
        {"query_nr": 7, "latency_ms": 5.0, "row_count": 100, "result_digest": "sha256:wrong"},
    ]
    out = OfficialTpcdsEvaluator().evaluate(_artifacts(tmp_path, queries, {"scale_factor": 1.0}))
    assert out["tpc-ds.queries_passed"].value == 0.5
    assert out["tpc-ds.queries_passed"].sample_count == 2


def test_correctness_omitted_when_not_sf1(tmp_path: Path) -> None:
    queries = [{"query_nr": 3, "latency_ms": 5.0, "row_count": 89, "result_digest": _ref_digest(3)}]
    out = OfficialTpcdsEvaluator().evaluate(_artifacts(tmp_path, queries, {"scale_factor": 10.0}))
    assert "tpc-ds.queries_passed" not in out  # reference is SF1-only
    assert "tpc-ds.total_runtime_ms" in out  # perf still reported at any SF


def test_malformed_latency_omits_perf_but_not_correctness(tmp_path: Path) -> None:
    queries = [
        {"query_nr": 3, "latency_ms": "oops", "row_count": 89, "result_digest": _ref_digest(3)},
    ]
    out = OfficialTpcdsEvaluator().evaluate(_artifacts(tmp_path, queries, {"scale_factor": 1.0}))
    assert "tpc-ds.total_runtime_ms" not in out
    assert "tpc-ds.geomean_latency_ms" not in out
    assert out["tpc-ds.queries_passed"].value == 1.0  # digest still matches


def test_nonpositive_latency_never_raises_and_still_scores_correctness(tmp_path: Path) -> None:
    # A degenerate/crafted artifact with a 0 latency must not raise (math.log
    # domain error) — geomean is omitted, total still reported, correctness holds.
    queries = [
        {"query_nr": 3, "latency_ms": 0.0, "row_count": 89, "result_digest": _ref_digest(3)},
    ]
    out = OfficialTpcdsEvaluator().evaluate(_artifacts(tmp_path, queries, {"scale_factor": 1.0}))
    assert "tpc-ds.geomean_latency_ms" not in out  # no positive latency → omitted
    assert out["tpc-ds.total_runtime_ms"].value == 0.0
    assert out["tpc-ds.queries_passed"].value == 1.0


def test_empty_or_missing_queries_returns_empty(tmp_path: Path) -> None:
    out = OfficialTpcdsEvaluator().evaluate(_artifacts(tmp_path, [], {"scale_factor": 1.0}))
    assert out == {}


def test_evaluate_over_the_committed_mock_fixture() -> None:
    # The suite's own mock_artifacts should score 1.0 (mock digests == reference).
    from clousight_bench.suites.tpc_ds.suite import TpcdsSuite

    raw = TpcdsSuite().mock_artifacts({})
    out = OfficialTpcdsEvaluator().evaluate(raw)
    assert out["tpc-ds.queries_passed"].value == 1.0
    assert out["tpc-ds.total_runtime_ms"].value > 0


def test_correctness_note_is_pinned_reference_not_verified(tmp_path):
    """TPC-DS has no official answer set in duckdb — its note must NEVER claim
    verification against official answers (guards the all_verified logic)."""
    ref = json.loads(
        (
            Path(__file__).resolve().parent.parent
            / "src/clousight_bench/suites/tpc_ds/fixtures/reference/sf1_digests.json"
        ).read_text()
    )
    queries = [{"query_nr": 1, "latency_ms": 5.0, "row_count": 1, "result_digest": ref["1"]["result_digest"]}]
    out = OfficialTpcdsEvaluator().evaluate(_artifacts(tmp_path, queries, {"scale_factor": 1.0}))
    notes = out["tpc-ds.queries_passed"].notes
    assert "pinned-reference reproducibility" in notes
    assert "verified against the official answer set" not in notes
