#!/usr/bin/env python
"""Capture the pinned SF1 TPC-DS reference digests for the tpc-ds suite.

DuckDB's ``tpcds_answers()`` is unusable (no params, defaults to SF10, errors),
so the suite verifies correctness against a pinned reference: the normalized
digest of each SF1 query result, produced HERE with the suite's OWN
``result_digest`` / ``run_query_set`` helpers (so runtime and reference always
agree). Re-run on any duckdb/extension upgrade and bump ``_SUITE_VERSION``.

    uv run --no-sync python scripts/capture_tpcds_reference.py

Writes src/clousight_bench/suites/tpc_ds/fixtures/reference/sf1_digests.json
(all 99 queries) and refreshes fixtures/mock/{queries,summary}.json (a 3-query
sample drawn from the same real run, so a mock evaluator run scores 1.0).
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from clousight_bench.suites.tpc_ds.suite import _ALL_QUERY_IDS, run_query_set

_FIXTURES = Path(__file__).resolve().parent.parent / "src/clousight_bench/suites/tpc_ds/fixtures"
_MOCK_QUERY_IDS = [3, 7, 42]


def main() -> None:
    con = duckdb.connect()
    con.execute("INSTALL tpcds; LOAD tpcds;")
    con.execute("CALL dsdgen(sf := 1)")
    ext = con.execute(
        "SELECT extension_version FROM duckdb_extensions() WHERE extension_name='tpcds'"
    ).fetchone()
    rows = run_query_set(con, list(_ALL_QUERY_IDS))
    con.close()

    # Reference: query_nr -> {result_digest, row_count}. (latency is not pinned.)
    reference = {
        str(r["query_nr"]): {"result_digest": r["result_digest"], "row_count": r["row_count"]}
        for r in rows
    }
    ref_path = _FIXTURES / "reference" / "sf1_digests.json"
    ref_path.write_text(json.dumps(reference, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {ref_path} ({len(reference)} queries)")

    # Mock fixture: a 3-query slice of the SAME real run (digests match the
    # reference, so the mock evaluator path scores queries_passed == 1.0).
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
