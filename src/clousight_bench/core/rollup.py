"""Downsample a run's ``series.parquet`` into time-bucketed aggregates.

Reads the long-table ``series.parquet`` produced by :class:`ResultStore` and
writes ``series_rollup.parquet`` (avg / p99 / max / count per series per time
bucket) so a chart or report can be rendered without scanning every raw sample.
Requires the ``[store]`` extra (duckdb + pyarrow).
"""
from __future__ import annotations

from pathlib import Path

_ROLLUP_SQL = """
    SELECT series,
           CAST(floor(t / ?) AS BIGINT) AS bucket,
           avg(value)               AS avg,
           quantile_cont(value, 0.99) AS p99,
           max(value)               AS max,
           count(*)                 AS n
    FROM read_parquet(?)
    GROUP BY series, bucket
    ORDER BY series, bucket
"""


def rollup(run_dir: Path | str, bucket_s: int = 1) -> Path:
    """Roll up ``run_dir/series.parquet`` into ``run_dir/series_rollup.parquet``.

    ``bucket_s`` is the bucket width in seconds. Returns the output path.
    Raises ``FileNotFoundError`` if there is no series file, and ``ImportError``
    (with a hint) if the ``[store]`` extra is not installed.
    """
    run_dir = Path(run_dir)
    src = run_dir / "series.parquet"
    if not src.exists():
        raise FileNotFoundError(f"no series.parquet in {run_dir}")
    try:
        import duckdb
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "rollup needs the [store] extra: pip install clousight-bench[store]"
        ) from exc

    out = run_dir / "series_rollup.parquet"
    con = duckdb.connect()
    table = con.execute(_ROLLUP_SQL, [bucket_s, str(src)]).to_arrow_table()
    pq.write_table(table, out)
    return out
