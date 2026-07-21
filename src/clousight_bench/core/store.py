"""ResultStore: persistence layer for records + time-series + artifacts.

record.json keeps the historical layout so report.py and older readers keep
working. When the optional [store] extra (duckdb + pyarrow) is installed and a
record carries series, the series is externalized to a per-run Parquet long
table and the record's `series` field becomes a pointer. Without the extra the
series stays inline in record.json (lossless at small scale).

Long-table columns (the stable handshake for cb-dataservice / SaaS web):
    run_id | domain | task_id | platform | config_hash | series | t | value | unit
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clousight_bench.core.schema import ResultRecord

try:  # optional [store] extra
    import duckdb  # noqa: F401
    import pyarrow  # noqa: F401

    STORE_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on install extras
    STORE_AVAILABLE = False

_LONG_COLUMNS = [
    "run_id", "domain", "task_id", "platform", "config_hash", "series", "t", "value", "unit",
]


class ResultStore:
    def __init__(self, results_dir: Path) -> None:
        self.results_dir = Path(results_dir)

    def _record_path(self, rec: ResultRecord) -> Path:
        out_dir = self.results_dir / rec.domain / rec.platform
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"{rec.task_id}-{rec.run_id}.json"

    def _run_dir(self, rec: ResultRecord) -> Path:
        return self.results_dir / rec.domain / rec.platform / rec.run_id

    def persist(self, record: ResultRecord) -> Path:
        payload = record.to_dict()
        if STORE_AVAILABLE and record.series:
            rel = self._write_series_parquet(record)
            payload["series"] = {"$parquet": rel}
        path = self._record_path(record)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def _write_series_parquet(self, record: ResultRecord) -> str:
        import pyarrow as pa
        import pyarrow.parquet as pq

        rows: dict[str, list] = {c: [] for c in _LONG_COLUMNS}
        for series_name, points in record.series.items():
            for t, value in points:
                rows["run_id"].append(record.run_id)
                rows["domain"].append(record.domain)
                rows["task_id"].append(record.task_id)
                rows["platform"].append(record.platform)
                rows["config_hash"].append(record.config_hash)
                rows["series"].append(series_name)
                rows["t"].append(t)
                rows["value"].append(float(value))
                rows["unit"].append(record.metrics.get(f"{series_name}__unit", ""))
        run_dir = self._run_dir(record)
        run_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = run_dir / "series.parquet"
        pq.write_table(pa.table(rows), parquet_path)
        return str(parquet_path.relative_to(self.results_dir))

    def query_series(self, sql: str | None = None, glob: str = "**/series.parquet") -> list[dict[str, Any]]:
        if not STORE_AVAILABLE:
            raise ImportError(
                "query_series needs the [store] extra: pip install clousight-bench[store]"
            )
        import duckdb

        pattern = str(self.results_dir / glob)
        con = duckdb.connect()
        con.execute(f"CREATE VIEW series AS SELECT * FROM read_parquet('{pattern}')")
        query = sql or "SELECT * FROM series"
        cur = con.execute(query)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
