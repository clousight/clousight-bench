import pyarrow as pa
import pyarrow.parquet as pq

from clousight_bench.core.rollup import rollup


def _write_rows(run_dir, series, t, value):
    run_dir.mkdir(parents=True, exist_ok=True)
    n = len(t)
    rows = {
        "run_id": ["r"] * n, "domain": ["d"] * n, "task_id": ["t"] * n,
        "platform": ["p"] * n, "config_hash": ["h"] * n,
        "series": series, "t": t, "value": value, "unit": [""] * n,
    }
    pq.write_table(pa.table(rows), run_dir / "series.parquet")


def test_rollup_buckets_reduce_rows(tmp_path):
    run_dir = tmp_path / "run-x"
    _write_rows(run_dir, series=["latency_ms"] * 6,
                t=[0.1, 0.2, 0.9, 1.1, 1.2, 1.9],
                value=[10.0, 20.0, 30.0, 40.0, 50.0, 60.0])
    out = rollup(run_dir, bucket_s=1)
    assert out.exists()
    table = pq.read_table(out).to_pydict()
    # 6 raw points spanning t in [0,1] -> 2 one-second buckets
    assert len(table["bucket"]) == 2
    assert set(table["series"]) == {"latency_ms"}
    assert "avg" in table and "p99" in table and "max" in table


def test_rollup_missing_series_parquet_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        rollup(tmp_path / "no-run", bucket_s=1)


def test_rollup_empty_series_yields_empty_rollup(tmp_path):
    run_dir = tmp_path / "run-empty"
    _write_rows(run_dir, series=[], t=[], value=[])
    out = rollup(run_dir, bucket_s=1)
    table = pq.read_table(out).to_pydict()
    assert table["bucket"] == []
    assert table["series"] == []


def test_rollup_mixed_series_grouped_independently(tmp_path):
    run_dir = tmp_path / "run-mixed"
    _write_rows(
        run_dir,
        series=["latency_ms", "latency_ms", "cost_usd", "cost_usd"],
        t=[0.1, 0.9, 0.2, 1.5],
        value=[10.0, 20.0, 1.0, 2.0],
    )
    out = rollup(run_dir, bucket_s=1)
    table = pq.read_table(out).to_pydict()
    assert set(table["series"]) == {"latency_ms", "cost_usd"}
    pairs = list(zip(table["series"], table["bucket"]))
    assert ("latency_ms", 0) in pairs
    assert ("cost_usd", 0) in pairs and ("cost_usd", 1) in pairs
