"""ResultStore: persist a ResultRecord 0.2 atomically, or say loudly where it went.

Record layout stays ``results/<domain>/<adapter>/<task_id>-<run_id>.json`` so
existing tooling keeps finding results. Writes are atomic, the payload carries
its own content digest, and an operator-identifying string is refused rather
than published. When the results directory cannot be written at all, the record
is dumped into the system temp directory and its absolute path is printed --
losing a completed measurement silently is the one failure mode this layer must
not have.

With the optional [store] extra (duckdb + pyarrow) a record's series is
externalized to a per-run Parquet long table and the record's ``series`` field
becomes a pointer. Long-table columns (the stable handshake for cb-dataservice
and the SaaS web):

    run_id | domain | task_id | platform | benchmark_fingerprint | series | t | value | unit
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from clousight_bench.core.fingerprints import record_digest
from clousight_bench.core.persistence import atomic_write_text, emergency_write_text
from clousight_bench.core.record import ResultRecord, StageError
from clousight_bench.core.redaction import SensitiveDataError, find_identity_leaks

try:  # optional [store] extra
    import duckdb  # noqa: F401
    import pyarrow  # noqa: F401

    STORE_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on install extras
    STORE_AVAILABLE = False

_LONG_COLUMNS = [
    "run_id", "domain", "task_id", "platform", "benchmark_fingerprint",
    "series", "t", "value", "unit",
]


class ResultStore:
    def __init__(self, results_dir: Path) -> None:
        self.results_dir = Path(results_dir)

    def _record_path(self, rec: ResultRecord) -> Path:
        out_dir = self.results_dir / rec.identity.domain / rec.identity.adapter
        return out_dir / f"{rec.identity.task_id}-{rec.run.run_id}.json"

    def _run_dir(self, rec: ResultRecord) -> Path:
        return self.results_dir / rec.identity.domain / rec.identity.adapter / rec.run.run_id

    def persist(self, record: ResultRecord) -> Path:
        series_pointer: dict[str, Any] | None = None
        if STORE_AVAILABLE and record.series and "$parquet" not in record.series:
            try:
                series_pointer = {"$parquet": self._write_series_parquet(record)}
            except OSError:
                # The sidecar is an optimisation. If it cannot be written we keep
                # the series inline rather than losing the observation.
                series_pointer = None

        record.run.stages["PERSIST"] = "ok"
        try:
            return atomic_write_text(
                self._record_path(record), self._render(record, series_pointer)
            )
        except OSError as exc:
            record.run.stages["PERSIST"] = "failed"
            record.errors.append(
                StageError(
                    stage="PERSIST",
                    code="persist_failed",
                    type=type(exc).__name__,
                    message=str(exc),
                    retryable=True,
                ).to_dict()
            )
            name = (
                f"{record.identity.domain}-{record.identity.task_id}"
                f"-{record.run.run_id}.json"
            )
            path = emergency_write_text(name, self._render(record, series_pointer))
            print(
                f"clousight-bench: could not write the results directory ({exc}); "
                f"emergency record written to {path}",
                file=sys.stderr,
            )
            return path

    def _render(
        self, record: ResultRecord, series_pointer: dict[str, Any] | None
    ) -> str:
        payload = record.to_dict()
        if series_pointer is not None:
            payload["series"] = series_pointer
        leaks = find_identity_leaks(payload)
        if leaks:
            raise SensitiveDataError(
                f"refusing to persist run {record.run.run_id}: operator-identifying "
                f"values at {leaks}"
            )
        digest = record_digest(payload)
        payload["fingerprints"]["record_digest"] = digest
        record.fingerprints.record_digest = digest
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def _write_series_parquet(self, record: ResultRecord) -> str:
        import pyarrow as pa
        import pyarrow.parquet as pq

        rows: dict[str, list] = {c: [] for c in _LONG_COLUMNS}
        for series_name, points in record.series.items():
            unit = str(record.measurements.get(series_name, {}).get("unit", ""))
            for t, value in points:
                rows["run_id"].append(record.run.run_id)
                rows["domain"].append(record.identity.domain)
                rows["task_id"].append(record.identity.task_id)
                rows["platform"].append(record.identity.adapter)
                rows["benchmark_fingerprint"].append(record.fingerprints.benchmark)
                rows["series"].append(series_name)
                rows["t"].append(t)
                rows["value"].append(float(value))
                rows["unit"].append(unit)
        run_dir = self._run_dir(record)
        run_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = run_dir / "series.parquet"
        pq.write_table(pa.table(rows), parquet_path)
        return str(parquet_path.relative_to(self.results_dir))

    def query_series(
        self, sql: str | None = None, glob: str = "**/series.parquet"
    ) -> list[dict[str, Any]]:
        if not STORE_AVAILABLE:
            raise ImportError(
                "query_series needs the [store] extra: pip install clousight-bench[store]"
            )
        import duckdb

        pattern = str(self.results_dir / glob)
        con = duckdb.connect()
        # Pass the (possibly glob) path via the relation API, not string
        # interpolation, so paths with quotes / special chars cannot break out
        # of the SQL (parameters aren't allowed inside CREATE VIEW read_parquet).
        con.read_parquet(pattern).create_view("series")
        cur = con.execute(sql or "SELECT * FROM series")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
