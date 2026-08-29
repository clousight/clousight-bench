#!/usr/bin/env python
"""Capture the pinned SF1 TPC-H reference digests for the tpc-h suite.

The reference is the normalized digest of each SF1 query result, produced with
the suite's OWN ``result_digest`` / ``run_query_set`` helpers (so runtime and
reference always agree). DuckDB ships a usable ``tpch_answers()`` SF1 set, so this
script ALSO cross-checks each captured digest against the official answer (parsed
+ normalized) and reports the match count — INFORMATIONAL only. Exact
answer-text-format normalization (CHAR space-padding, DuckDB's numeric
formatting) is not yet reconciled, so today the reference is a pinned-reference
reproducibility check like TPC-DS; reaching full official-answer verification is
a future upgrade. Re-run on any duckdb/extension upgrade and bump
``_SUITE_VERSION``.

    uv run --no-sync python scripts/capture_tpch_reference.py

Writes src/clousight_bench/suites/tpc_h/fixtures/reference/sf1_digests.json (all
22 queries) and refreshes fixtures/mock/{queries,summary}.json (a 3-query sample
from the same real run, so a mock evaluator run scores 1.0).
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from clousight_bench.suites.tpc_h.suite import _ALL_QUERY_IDS, result_digest, run_query_set

_FIXTURES = Path(__file__).resolve().parent.parent / "src/clousight_bench/suites/tpc_h/fixtures"
_MOCK_QUERY_IDS = [1, 6, 14]


def _official_answer_digest(con: duckdb.DuckDBPyConnection, nr: int) -> str | None:
    """Digest of DuckDB's official SF1 answer for query *nr*, normalized like a result.

    tpch_answers() returns pipe-delimited text (header line + data rows). We parse
    the data rows, coerce numeric-looking cells to float (so the shared 2dp
    rounding applies), and feed them through the SAME result_digest as a live run.
    Returns None if no SF1 answer exists.
    """
    row = con.execute(
        "SELECT answer FROM tpch_answers() WHERE scale_factor = 1 AND query_nr = ?", [nr]
    ).fetchone()
    if not row or not row[0]:
        return None
    lines = [ln for ln in str(row[0]).split("\n") if ln != ""]
    if len(lines) < 1:
        return None
    data_rows: list[tuple] = []
    for ln in lines[1:]:  # skip the header line
        cells: list[object] = []
        for cell in ln.split("|"):
            try:
                cells.append(float(cell))
            except ValueError:
                cells.append(cell)
        data_rows.append(tuple(cells))
    return result_digest(data_rows)


def main() -> None:
    con = duckdb.connect()
    con.execute("INSTALL tpch; LOAD tpch;")
    con.execute("CALL dbgen(sf := 1)")
    ext = con.execute(
        "SELECT extension_version FROM duckdb_extensions() WHERE extension_name='tpch'"
    ).fetchone()
    rows = run_query_set(con, list(_ALL_QUERY_IDS))

    # Cross-validate each captured digest against the official SF1 answer set.
    verified = 0
    mismatches: list[int] = []
    for r in rows:
        official = _official_answer_digest(con, r["query_nr"])
        if official is None:
            continue
        if official == r["result_digest"]:
            verified += 1
        else:
            mismatches.append(r["query_nr"])
    con.close()

    reference = {
        str(r["query_nr"]): {"result_digest": r["result_digest"], "row_count": r["row_count"]}
        for r in rows
    }
    ref_path = _FIXTURES / "reference" / "sf1_digests.json"
    ref_path.write_text(json.dumps(reference, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {ref_path} ({len(reference)} queries)")
    print(f"official-answer cross-check: {verified}/{len(rows)} verified against tpch_answers() SF1")
    if mismatches:
        print(f"  NOTE: query_nr not matching the official answer after normalization: {mismatches}")

    by_nr = {r["query_nr"]: r for r in rows}
    mock_queries = [dict(by_nr[nr]) for nr in _MOCK_QUERY_IDS]
    (_FIXTURES / "mock" / "queries.json").write_text(
        json.dumps(mock_queries, indent=2) + "\n", encoding="utf-8"
    )
    (_FIXTURES / "mock" / "summary.json").write_text(
        json.dumps(
            {
                "scale_factor": 1.0,
                "duckdb_version": duckdb.__version__,
                "extension_version": ext[0] if ext else "unknown",
                "query_count": len(mock_queries),
                "query_ids": _MOCK_QUERY_IDS,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote mock fixture ({len(mock_queries)} queries: {_MOCK_QUERY_IDS})")


if __name__ == "__main__":
    main()
