"""TPC-DS data-maintenance stand-in for DuckDB — clousight-generated, unaudited.

DuckDB's ``tpcds`` extension ships dsdgen + the 99 queries but none of the
spec's LF_* maintenance functions (they consume dsdgen refresh flat files). This
module exercises the same *kind* of write load with a self-contained, engine-
verifiable round-trip on the ``store_sales`` fact table:

* insert ``n`` rows cloned from existing ones with ticket numbers shifted past
  the current maximum (``SELECT * REPLACE`` keeps it schema-agnostic), then
* delete exactly that batch.

Each DM run is state-neutral (insert + delete), so DM1 and DM2 are identical in
shape and repeated runs never drift the dataset. The update set is
clousight-generated — the QphDS number that folds these timings is therefore
explicitly **unaudited** (as is the whole official mode).
"""

from __future__ import annotations

from typing import Any


def maintenance_rows(scale_factor: float) -> int:
    """Rows per DM batch: ``round(SF * 1000)``, min 1 (clousight-dm-v1 sizing)."""
    return max(1, round(float(scale_factor) * 1000))


def run_dm(con: Any, n_rows: int) -> None:
    """One data-maintenance run: insert a shifted clone batch, then delete it."""
    n = int(n_rows)
    row = con.execute("SELECT COALESCE(max(ss_ticket_number), 0) FROM store_sales").fetchone()
    base = int(row[0])
    con.execute(
        f"INSERT INTO store_sales "
        f"SELECT * REPLACE (ss_ticket_number + {base} AS ss_ticket_number) "
        f"FROM store_sales LIMIT {n}"
    )
    con.execute(f"DELETE FROM store_sales WHERE ss_ticket_number > {base}")
