"""The official-tpch-evaluator (pure function over RawArtifacts)."""

from __future__ import annotations

import json
import math
from pathlib import Path

from clousight_bench.core.registry import load_evaluators
from clousight_bench.core.suite import RawArtifacts
from clousight_bench.suites.tpc_h.evaluator import OfficialTpchEvaluator


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
            / "src/clousight_bench/suites/tpc_h/fixtures/reference/sf1_digests.json"
        ).read_text()
    )
    return ref[str(nr)]["result_digest"]


def test_registered_via_entry_point() -> None:
    evs = {e.evaluator_id: e for e in load_evaluators()}
    assert "official-tpch-evaluator" in evs
    assert isinstance(evs["official-tpch-evaluator"], OfficialTpchEvaluator)


def test_supports_only_tpch() -> None:
    ev = OfficialTpchEvaluator()
    assert ev.supports("tpc-h", "duckdb-local")
    assert not ev.supports("tpc-ds", "duckdb-local")


def test_perf_metrics_and_correctness_at_sf1(tmp_path: Path) -> None:
    queries = [
        {"query_nr": 1, "latency_ms": 10.0, "row_count": 4, "result_digest": _ref_digest(1)},
        {"query_nr": 6, "latency_ms": 40.0, "row_count": 1, "result_digest": _ref_digest(6)},
    ]
    out = OfficialTpchEvaluator().evaluate(_artifacts(tmp_path, queries, {"scale_factor": 1.0}))
    assert out["tpc-h.queries_passed"].value == 1.0
    assert out["tpc-h.queries_passed"].reproducibility_class == "deterministic"
    assert out["tpc-h.queries_passed"].official is True
    assert out["tpc-h.total_runtime_ms"].value == 50.0
    assert out["tpc-h.total_runtime_ms"].official is True
    assert math.isclose(out["tpc-h.geomean_latency_ms"].value, 20.0, rel_tol=1e-9)


def test_partial_correctness(tmp_path: Path) -> None:
    queries = [
        {"query_nr": 1, "latency_ms": 5.0, "row_count": 4, "result_digest": _ref_digest(1)},
        {"query_nr": 6, "latency_ms": 5.0, "row_count": 1, "result_digest": "sha256:wrong"},
    ]
    out = OfficialTpchEvaluator().evaluate(_artifacts(tmp_path, queries, {"scale_factor": 1.0}))
    assert out["tpc-h.queries_passed"].value == 0.5


def test_correctness_omitted_when_not_sf1(tmp_path: Path) -> None:
    queries = [{"query_nr": 1, "latency_ms": 5.0, "row_count": 4, "result_digest": _ref_digest(1)}]
    out = OfficialTpchEvaluator().evaluate(_artifacts(tmp_path, queries, {"scale_factor": 10.0}))
    assert "tpc-h.queries_passed" not in out
    assert "tpc-h.total_runtime_ms" in out


def test_nonpositive_latency_never_raises(tmp_path: Path) -> None:
    queries = [{"query_nr": 1, "latency_ms": 0.0, "row_count": 4, "result_digest": _ref_digest(1)}]
    out = OfficialTpchEvaluator().evaluate(_artifacts(tmp_path, queries, {"scale_factor": 1.0}))
    assert "tpc-h.geomean_latency_ms" not in out
    assert out["tpc-h.total_runtime_ms"].value == 0.0
    assert out["tpc-h.queries_passed"].value == 1.0


def test_evaluate_over_the_committed_mock_fixture() -> None:
    from clousight_bench.suites.tpc_h.suite import TpchSuite

    raw = TpchSuite().mock_artifacts({})
    out = OfficialTpchEvaluator().evaluate(raw)
    assert out["tpc-h.queries_passed"].value == 1.0
    assert out["tpc-h.total_runtime_ms"].value > 0


def test_correctness_at_sf01_via_the_multi_sf_reference(tmp_path: Path) -> None:
    """B6: correctness is SF-keyed — the shipped sf0.1 reference scores runs at SF 0.1."""
    ref = json.loads(
        (
            Path(__file__).resolve().parent.parent
            / "src/clousight_bench/suites/tpc_h/fixtures/reference/sf0.1_digests.json"
        ).read_text()
    )
    queries = [{"query_nr": 1, "latency_ms": 5.0, "row_count": 4, "result_digest": ref["1"]["result_digest"]}]
    out = OfficialTpchEvaluator().evaluate(_artifacts(tmp_path, queries, {"scale_factor": 0.1}))
    assert out["tpc-h.queries_passed"].value == 1.0
    assert "verified against the official answer set" in out["tpc-h.queries_passed"].notes


def test_verified_official_note_at_sf1(tmp_path: Path) -> None:
    queries = [{"query_nr": 1, "latency_ms": 5.0, "row_count": 4, "result_digest": _ref_digest(1)}]
    out = OfficialTpchEvaluator().evaluate(_artifacts(tmp_path, queries, {"scale_factor": 1.0}))
    assert "verified against the official answer set" in out["tpc-h.queries_passed"].notes
