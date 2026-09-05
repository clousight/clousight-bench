"""The official-tpch-qphh-evaluator: official.json -> QphH@Size measurements."""

from __future__ import annotations

import json
import math
from pathlib import Path

from clousight_bench.core.registry import load_evaluators
from clousight_bench.core.suite import RawArtifacts
from clousight_bench.suites.tpc_h.official_evaluator import OfficialTpchQphhEvaluator

_REF = (
    Path(__file__).resolve().parent.parent
    / "src/clousight_bench/suites/tpc_h/fixtures/reference/sf1_digests.json"
)


def _ref_digest(nr: int) -> str:
    return json.loads(_REF.read_text())[str(nr)]["result_digest"]


def _official(tmp_path: Path, doc: dict) -> RawArtifacts:
    (tmp_path / "official.json").write_text(json.dumps(doc))
    return RawArtifacts(dir=tmp_path, manifest={"official": {"path": "official.json", "rows": None}})


def _doc_sf1(*, streams: int = 2, elapsed_s: float = 158.4) -> dict:
    # 22 power queries at 1.0s each, RF at 1.0s -> geomean 1.0 -> Power = 3600*SF
    power_queries = [
        {"query_nr": nr, "interval_s": 1.0, "row_count": 1, "result_digest": _ref_digest(nr)}
        for nr in range(1, 23)
    ]
    tp_stream = [
        {"query_nr": nr, "interval_s": 0.5, "row_count": 1, "result_digest": "d"} for nr in range(1, 23)
    ]
    return {
        "scale_factor": 1.0,
        "streams": streams,
        "load": {"load_time_s": 12.3},
        "power": {"rf1_s": 1.0, "rf2_s": 1.0, "queries": power_queries},
        "throughput": {
            "elapsed_s": elapsed_s,
            "query_streams": [{"stream_id": s, "queries": tp_stream} for s in range(1, streams + 1)],
            "refresh_stream": [{"pair": p, "rf1_s": 0.4, "rf2_s": 0.3} for p in range(1, streams + 1)],
        },
        "acid": {"atomicity": "pass", "consistency": "pass", "isolation": "fail", "durability": "n/a"},
        "engine": {"duckdb_version": "1.5.4", "extension_version": "x"},
    }


def test_registered_via_entry_point() -> None:
    evs = {e.evaluator_id: e for e in load_evaluators()}
    assert "official-tpch-qphh-evaluator" in evs
    assert isinstance(evs["official-tpch-qphh-evaluator"], OfficialTpchQphhEvaluator)


def test_supports_only_tpch() -> None:
    ev = OfficialTpchQphhEvaluator()
    assert ev.supports("tpc-h", "duckdb-local")
    assert not ev.supports("tpc-ds", "duckdb-local")


def test_composite_metrics(tmp_path: Path) -> None:
    out = OfficialTpchQphhEvaluator().evaluate(_official(tmp_path, _doc_sf1()))
    assert math.isclose(out["tpc-h.load_time_s"].value, 12.3)
    assert out["tpc-h.load_time_s"].unit == "s"
    assert math.isclose(out["tpc-h.power_at_size"].value, 3600.0, rel_tol=1e-9)
    assert math.isclose(out["tpc-h.throughput_at_size"].value, 1000.0, rel_tol=1e-9)
    assert math.isclose(out["tpc-h.qphh_at_size"].value, math.sqrt(3_600_000.0), rel_tol=1e-9)
    q = out["tpc-h.qphh_at_size"]
    assert q.unit == "QphH"
    assert q.reproducibility_class == "environmental"
    assert q.official is True
    assert "unaudited" in q.notes


def test_no_correctness_claim_in_official_mode(tmp_path: Path) -> None:
    """Power queries run AFTER RF1 refreshed the data — comparing them against
    pristine references would mislabel correct behavior as failure, so the
    official evaluator deliberately makes no correctness claim at ANY SF."""
    for sf in (1.0, 10.0):
        doc = _doc_sf1()
        doc["scale_factor"] = sf
        out = OfficialTpchQphhEvaluator().evaluate(_official(tmp_path, doc))
        assert "tpc-h.queries_passed" not in out
        assert "tpc-h.qphh_at_size" in out  # perf composites unaffected


def test_acid_pass_fail_and_durability_omitted(tmp_path: Path) -> None:
    out = OfficialTpchQphhEvaluator().evaluate(_official(tmp_path, _doc_sf1()))
    assert out["tpc-h.acid_atomicity"].value == 1.0
    assert out["tpc-h.acid_consistency"].value == 1.0
    assert out["tpc-h.acid_isolation"].value == 0.0  # "fail" in fixture
    assert "tpc-h.acid_durability" not in out


def test_missing_sections_never_raise(tmp_path: Path) -> None:
    out = OfficialTpchQphhEvaluator().evaluate(_official(tmp_path, {"scale_factor": 1.0}))
    assert out == {}  # nothing to score, no raise


def test_broken_artifact_never_raises(tmp_path: Path) -> None:
    (tmp_path / "official.json").write_text("{not json")
    raw = RawArtifacts(dir=tmp_path, manifest={"official": {"path": "official.json", "rows": None}})
    assert OfficialTpchQphhEvaluator().evaluate(raw) == {}
