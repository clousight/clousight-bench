"""ResultStore: record.json 兼容布局 + 可选 Parquet series 外置。"""
import json

import pytest

from clousight_bench.core.schema import ResultRecord, utc_now
from clousight_bench.core.store import STORE_AVAILABLE, ResultStore


def _rec(series=None) -> ResultRecord:
    return ResultRecord(
        domain="agent-runtime", task_id="T1.3", platform="local-sim", run_id="run-x",
        started_at=utc_now(), finished_at=utc_now(),
        config_hash="sha256:abc", evidence_layer="C", metrics={"p99_ms": 9},
        series=series or {},
    )


def test_persist_keeps_backward_compatible_record_path(tmp_path):
    store = ResultStore(tmp_path)
    path = store.persist(_rec())
    expected = tmp_path / "agent-runtime" / "local-sim" / "T1.3-run-x.json"
    assert path == expected
    assert expected.exists()
    data = json.loads(expected.read_text())
    assert data["metrics"]["p99_ms"] == 9


@pytest.mark.skipif(not STORE_AVAILABLE, reason="requires [store] extra")
def test_series_externalized_to_parquet_and_queryable(tmp_path):
    store = ResultStore(tmp_path)
    store.persist(_rec(series={"latency_ms": [[1, 10.0], [2, 20.0]]}))
    parquet = tmp_path / "agent-runtime" / "local-sim" / "run-x" / "series.parquet"
    assert parquet.exists()
    record_json = json.loads(
        (tmp_path / "agent-runtime" / "local-sim" / "T1.3-run-x.json").read_text()
    )
    assert record_json["series"] == {"$parquet": "agent-runtime/local-sim/run-x/series.parquet"}
    rows = store.query_series("SELECT series, count(*) AS n FROM series GROUP BY series")
    assert rows == [{"series": "latency_ms", "n": 2}]


def test_series_inline_when_store_unavailable(tmp_path, monkeypatch):
    import clousight_bench.core.store as store_mod
    monkeypatch.setattr(store_mod, "STORE_AVAILABLE", False)
    store = store_mod.ResultStore(tmp_path)
    store.persist(_rec(series={"latency_ms": [[1, 10.0]]}))
    record_json = json.loads(
        (tmp_path / "agent-runtime" / "local-sim" / "T1.3-run-x.json").read_text()
    )
    assert record_json["series"] == {"latency_ms": [[1, 10.0]]}
