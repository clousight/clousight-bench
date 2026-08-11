"""Unified local query/analysis over persisted results.

Read-time flatten: every query scans the result JSONs (digest-verified, same
rule as store.query_series), flattens them into records/measurements/findings
rows, and joins the existing series.parquet sidecars. JSON stays the single
source of truth; nothing new is persisted unless you export.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from clousight_bench.core.fingerprints import record_digest

_COLUMNS: dict[str, list[str]] = {
    "records": [
        "run_id",
        "domain",
        "task_id",
        "platform",
        "task_revision",
        "scorer_revision",
        "status",
        "started_at",
        "finished_at",
        "benchmark_fp",
        "environment_fp",
        "implementation_fp",
        "record_digest",
        "region",
        "mode",
        "execution",
        "cost_usd",
        "list_cost_usd",
        "discount_usd",
    ],
    "measurements": [
        "run_id",
        "domain",
        "task_id",
        "platform",
        "benchmark_fp",
        "environment_fp",
        "name",
        "value_num",
        "value_str",
        "unit",
        "evidence",
        "aggregation",
        "sample_count",
    ],
    "findings": ["run_id", "domain", "task_id", "platform", "code", "severity", "summary", "evidence"],
    "series": [
        "run_id",
        "domain",
        "task_id",
        "platform",
        "benchmark_fingerprint",
        "series",
        "t",
        "value",
        "unit",
    ],
}


def iter_verified_records(results_dir: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield (path, payload) for each result JSON whose record_digest verifies.

    Skips run_plan aggregates (results/aggregates/**), non-dict payloads,
    unreadable files, and any record whose recomputed digest != stored digest.
    """
    root = Path(results_dir)
    agg = (root / "aggregates").resolve()
    campaigns = (root / "campaigns").resolve()
    for record_path in sorted(root.rglob("*.json")):
        try:
            resolved = record_path.resolve()
            if agg in resolved.parents or campaigns in resolved.parents:
                continue
            payload = json.loads(record_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            expected = payload.get("fingerprints", {}).get("record_digest")
            if not isinstance(expected, str) or record_digest(payload) != expected:
                continue
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        yield record_path, payload


def _num_str(value: Any) -> tuple[float | None, str | None]:
    """Split a measurement value into (value_num, value_str)."""
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, str(value)
    if isinstance(value, (int, float)):
        return float(value), None
    return None, str(value)


def _as_columns(rows: list[dict[str, Any]], columns: list[str]) -> dict[str, list]:
    """Column-oriented dict with a fixed schema, so an empty result still
    registers a typed, correctly-named view/table."""
    out: dict[str, list] = {c: [] for c in columns}
    for row in rows:
        for c in columns:
            out[c].append(row.get(c))
    return out


class Analytics:
    """Read-time flatten + SQL query + export over a results directory."""

    def __init__(self, results_dir: Path) -> None:
        self.results_dir = Path(results_dir)

    def flatten(self, table: str) -> list[dict[str, Any]]:
        if table == "records":
            return list(self._records())
        if table == "measurements":
            return list(self._measurements())
        if table == "findings":
            return list(self._findings())
        if table == "series":
            return self._series_rows()
        raise ValueError(f"unknown table {table!r}")

    def _records(self) -> Iterator[dict[str, Any]]:
        for _p, rec in iter_verified_records(self.results_dir):
            ident = rec.get("identity", {})
            env = rec.get("environment", {})
            fp = rec.get("fingerprints", {})
            run = rec.get("run", {})
            pricing = rec.get("extensions", {}).get("pricing", {})
            yield {
                "run_id": run.get("run_id"),
                "domain": ident.get("domain"),
                "task_id": ident.get("task_id"),
                "platform": ident.get("adapter"),
                "task_revision": ident.get("task_revision"),
                "scorer_revision": ident.get("scorer_revision"),
                "status": rec.get("status"),
                "started_at": run.get("started_at"),
                "finished_at": run.get("finished_at"),
                "benchmark_fp": fp.get("benchmark"),
                "environment_fp": fp.get("environment"),
                "implementation_fp": fp.get("implementation"),
                "record_digest": fp.get("record_digest"),
                "region": env.get("region"),
                "mode": env.get("mode"),
                "execution": env.get("execution", "unknown"),
                "cost_usd": pricing.get("cost_usd") if isinstance(pricing, dict) else None,
                "list_cost_usd": pricing.get("list_cost_usd") if isinstance(pricing, dict) else None,
                "discount_usd": pricing.get("discount_usd") if isinstance(pricing, dict) else None,
            }

    def _measurements(self) -> Iterator[dict[str, Any]]:
        for _p, rec in iter_verified_records(self.results_dir):
            ident = rec.get("identity", {})
            fp = rec.get("fingerprints", {})
            run = rec.get("run", {})
            for name, m in (rec.get("measurements") or {}).items():
                if not isinstance(m, dict):
                    continue
                num, s = _num_str(m.get("value"))
                yield {
                    "run_id": run.get("run_id"),
                    "domain": ident.get("domain"),
                    "task_id": ident.get("task_id"),
                    "platform": ident.get("adapter"),
                    "benchmark_fp": fp.get("benchmark"),
                    "environment_fp": fp.get("environment"),
                    "name": name,
                    "value_num": num,
                    "value_str": s,
                    "unit": m.get("unit", ""),
                    "evidence": m.get("evidence", ""),
                    "aggregation": m.get("aggregation", ""),
                    "sample_count": m.get("sample_count"),
                }

    def _findings(self) -> Iterator[dict[str, Any]]:
        for _p, rec in iter_verified_records(self.results_dir):
            ident = rec.get("identity", {})
            run = rec.get("run", {})
            for f in rec.get("findings") or []:
                if not isinstance(f, dict):
                    continue
                yield {
                    "run_id": run.get("run_id"),
                    "domain": ident.get("domain"),
                    "task_id": ident.get("task_id"),
                    "platform": ident.get("adapter"),
                    "code": f.get("code"),
                    "severity": f.get("severity"),
                    "summary": f.get("summary"),
                    "evidence": f.get("evidence"),
                }

    def _series_rows(self) -> list[dict[str, Any]]:
        """Rows from every verified record's series.parquet sidecar (needs pyarrow)."""
        from clousight_bench.core.store import validate_sidecar

        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ImportError(
                "series analytics needs the [store] extra: pip install clousight-bench[store]"
            ) from exc
        rows: list[dict[str, Any]] = []
        for _p, payload in iter_verified_records(self.results_dir):
            sidecar, error = validate_sidecar(self.results_dir, payload)
            if error is not None or sidecar is None:
                continue
            table = pq.read_table(sidecar)
            rows.extend(table.to_pylist())
        return rows

    def query(self, sql: str) -> list[dict[str, Any]]:
        try:
            import duckdb
            import pyarrow as pa
        except ImportError as exc:
            raise ImportError("query needs the [store] extra: pip install clousight-bench[store]") from exc
        con = duckdb.connect()
        try:
            for view in ("records", "measurements", "findings", "series"):
                rows = self.flatten(view)
                table = pa.table(_as_columns(rows, _COLUMNS[view]))
                con.register(f"_{view}", table)
                con.execute(f"CREATE VIEW {view} AS SELECT * FROM _{view}")
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            con.close()

    def export(self, table: str, out: Path, fmt: str = "parquet") -> Path:
        out = Path(out)
        rows = self.flatten(table)
        columns = _COLUMNS[table]
        if fmt == "jsonl":
            with out.open("w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps({c: row.get(c) for c in columns}, ensure_ascii=False))
                    fh.write("\n")
        elif fmt == "csv":
            import csv

            with out.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=columns)
                writer.writeheader()
                for row in rows:
                    writer.writerow({c: row.get(c) for c in columns})
        elif fmt == "parquet":
            try:
                import pyarrow as pa
                import pyarrow.parquet as pq
            except ImportError as exc:
                raise ImportError(
                    "parquet export needs the [store] extra: pip install clousight-bench[store]"
                ) from exc
            pq.write_table(pa.table(_as_columns(rows, columns)), out)
        else:
            raise ValueError(f"unknown format {fmt!r} (parquet | csv | jsonl)")
        return out
