#!/usr/bin/env python
"""Capture pinned TPC-H reference digests, verified against the official answers.

For each scale factor (default: 1, 0.1, 0.01) this script generates the data,
runs all 22 queries with the suite's OWN ``run_query_set`` / ``result_digest``
helpers (so runtime and reference always agree), and VERIFIES each live result
cell-by-cell against DuckDB's bundled official answer set (``tpch_answers()``,
which ships exactly these three scale factors):

- numeric-looking cells compare at the shared 2-dp rounding (the digest's own
  ``canon_value`` rule), other cells compare stripped;
- rows compare order-insensitively;
- an all-NULL aggregate row equals an empty official answer (q17 at SF0.01:
  ``avg(...)`` over zero rows is one NULL row; the answer file prints nothing).

Verification is cell-level (not digest-level) because the pinned digest is
type-sensitive by design (``Decimal('380456.00')`` != ``380456``); the digest
stays the runtime artifact-comparison mechanism, and the per-query
``verified_official`` flag records that the pinned digest was captured from a
result PROVEN equal to the official answer. The script fails loud if any query
with an available official answer does not verify.

Writes ``reference/sf{sf:g}_digests.json`` per scale factor and refreshes
``fixtures/mock/{queries,summary}.json`` from the SF1 run. Re-run on any
duckdb/extension upgrade and bump ``_SUITE_VERSION``.

    uv run --no-sync python scripts/capture_tpch_reference.py [--sf 1 --sf 0.1]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb

from clousight_bench.suites.tpc_h.suite import _ALL_QUERY_IDS, run_query_set

_FIXTURES = Path(__file__).resolve().parent.parent / "src/clousight_bench/suites/tpc_h/fixtures"
_MOCK_QUERY_IDS = [1, 6, 14]
_DEFAULT_SFS = (1.0, 0.1, 0.01)


def _norm_cell(value: object) -> str:
    if value is None:
        return "\\N"
    s = str(value).strip()
    if s in ("", "NULL"):
        return "\\N"
    try:
        return f"{float(s):.2f}"
    except ValueError:
        return s


def _norm_rows(rows: list[tuple]) -> list[str]:
    out = []
    for row in rows:
        cells = [_norm_cell(c) for c in row]
        if all(c == "\\N" for c in cells):
            continue  # an all-NULL aggregate row == an empty official answer
        out.append("\x1f".join(cells))
    return sorted(out)


def _parse_answer(text: str) -> list[tuple]:
    lines = [ln for ln in str(text).split("\n") if ln != ""]
    return [tuple(ln.split("|")) for ln in lines[1:]]  # skip the header line


def verify_against_official(
    con: duckdb.DuckDBPyConnection, sf: float, nr: int, live_rows: list[tuple]
) -> bool | None:
    """True/False = verified/mismatch against ``tpch_answers()``; None = no answer."""
    row = con.execute(
        "SELECT answer FROM tpch_answers() WHERE scale_factor = ? AND query_nr = ?", [sf, nr]
    ).fetchone()
    if not row or row[0] is None:
        return None
    return _norm_rows(_parse_answer(row[0])) == _norm_rows(live_rows)


def capture_sf(sf: float) -> tuple[dict, dict, str]:
    """Run all queries at *sf*; return (reference dict, raw rows by nr, ext version)."""
    con = duckdb.connect()
    con.execute("INSTALL tpch; LOAD tpch;")
    con.execute("CALL dbgen(sf := ?)", [sf])
    ext = con.execute(
        "SELECT extension_version FROM duckdb_extensions() WHERE extension_name='tpch'"
    ).fetchone()
    rows = run_query_set(con, list(_ALL_QUERY_IDS))
    reference: dict[str, dict] = {}
    unverified: list[int] = []
    for r in rows:
        nr = r["query_nr"]
        live = con.execute(f"PRAGMA tpch({nr})").fetchall()
        verdict = verify_against_official(con, sf, nr, live)
        if verdict is False:
            unverified.append(nr)
        reference[str(nr)] = {
            "result_digest": r["result_digest"],
            "row_count": r["row_count"],
            "verified_official": bool(verdict),
        }
    con.close()
    if unverified:
        print(f"FAIL sf={sf:g}: queries not matching the official answer: {unverified}")
        sys.exit(1)
    return reference, {r["query_nr"]: r for r in rows}, ext[0] if ext else "unknown"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sf", action="append", type=float, help="scale factor (repeatable)")
    args = ap.parse_args()
    sfs = tuple(args.sf) if args.sf else _DEFAULT_SFS

    for sf in sfs:
        reference, by_nr, ext_version = capture_sf(sf)
        ref_path = _FIXTURES / "reference" / f"sf{sf:g}_digests.json"
        ref_path.write_text(json.dumps(reference, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        verified = sum(1 for v in reference.values() if v["verified_official"])
        print(f"wrote {ref_path} ({len(reference)} queries, {verified} verified official)")

        if sf == 1.0:
            mock_queries = [dict(by_nr[nr]) for nr in _MOCK_QUERY_IDS]
            (_FIXTURES / "mock" / "queries.json").write_text(
                json.dumps(mock_queries, indent=2) + "\n", encoding="utf-8"
            )
            (_FIXTURES / "mock" / "summary.json").write_text(
                json.dumps(
                    {
                        "scale_factor": 1.0,
                        "duckdb_version": duckdb.__version__,
                        "extension_version": ext_version,
                        "query_count": len(mock_queries),
                        "query_ids": _MOCK_QUERY_IDS,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            print("refreshed mock fixtures from the SF1 run")


if __name__ == "__main__":
    main()
