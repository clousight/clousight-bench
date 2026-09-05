"""Best-effort ACID probes for the DuckDB TPC-H reference (unaudited).

Adapts the TPC-H ACID tests (spec clause 3) to what an embedded DuckDB can
actually demonstrate, returning a ``pass``/``fail`` per property:

* **Atomicity** — a mutating transaction is rolled back (must leave data
  unchanged) and another is committed (must apply, then is restored).
* **Consistency** — the TPC-H condition ``O_TOTALPRICE == derived lineitem sum``
  is established, then protected against a rolled-back inconsistent change.
* **Isolation** — a reader in an open transaction keeps a stable snapshot while a
  second connection commits an update (snapshot isolation). ``"n/a"`` when no
  second-connection factory is available (e.g. in-memory).
* **Durability** — always ``"n/a"``: no crash/recovery harness on embedded DuckDB.

Engine-agnostic at import; the caller passes the connection (and an optional
``open_conn`` factory that returns a fresh connection to the same database).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _first_orderkey(con: Any) -> int:
    return int(con.execute("SELECT min(o_orderkey) FROM orders").fetchone()[0])


def _totalprice(con: Any, k: int) -> Any:
    return con.execute("SELECT o_totalprice FROM orders WHERE o_orderkey=?", [k]).fetchone()[0]


def _derived_sum(con: Any, k: int) -> Any:
    return con.execute(
        "SELECT COALESCE(CAST(SUM(l_extendedprice*(1-l_discount)*(1+l_tax)) AS DECIMAL(15,2)),0) "
        "FROM lineitem WHERE l_orderkey=?",
        [k],
    ).fetchone()[0]


def check_atomicity(con: Any) -> bool:
    k = _first_orderkey(con)
    v0 = _totalprice(con, k)
    con.execute("BEGIN TRANSACTION")
    con.execute("UPDATE orders SET o_totalprice=o_totalprice+1 WHERE o_orderkey=?", [k])
    con.execute("ROLLBACK")
    rolled_back_ok = _totalprice(con, k) == v0

    con.execute("BEGIN TRANSACTION")
    con.execute("UPDATE orders SET o_totalprice=o_totalprice+1 WHERE o_orderkey=?", [k])
    con.execute("COMMIT")
    committed_ok = _totalprice(con, k) != v0
    con.execute("UPDATE orders SET o_totalprice=? WHERE o_orderkey=?", [v0, k])  # restore
    restored_ok = _totalprice(con, k) == v0
    return rolled_back_ok and committed_ok and restored_ok


def check_consistency(con: Any) -> bool:
    k = _first_orderkey(con)
    v0 = _totalprice(con, k)
    d = _derived_sum(con, k)
    con.execute("UPDATE orders SET o_totalprice=? WHERE o_orderkey=?", [d, k])  # establish
    established_ok = _totalprice(con, k) == d

    con.execute("BEGIN TRANSACTION")
    con.execute("UPDATE orders SET o_totalprice=o_totalprice+9999 WHERE o_orderkey=?", [k])
    con.execute("ROLLBACK")
    protected_ok = _totalprice(con, k) == d

    con.execute("UPDATE orders SET o_totalprice=? WHERE o_orderkey=?", [v0, k])  # restore
    return established_ok and protected_ok


def check_isolation(con: Any, open_conn: Callable[[], Any] | None) -> bool | None:
    if open_conn is None:
        return None
    k = _first_orderkey(con)
    v0 = _totalprice(con, k)
    con.execute("BEGIN TRANSACTION")
    try:
        r1 = _totalprice(con, k)
        other = open_conn()
        try:
            other.execute("UPDATE orders SET o_totalprice=o_totalprice+1000 WHERE o_orderkey=?", [k])
        finally:
            other.close()
        r2 = _totalprice(con, k)  # same snapshot -> unchanged
    finally:
        con.execute("ROLLBACK")
    con.execute("UPDATE orders SET o_totalprice=? WHERE o_orderkey=?", [v0, k])  # restore
    return r1 == r2


def run_acid(con: Any, open_conn: Callable[[], Any] | None = None) -> dict[str, str]:
    """Run A/C/I probes → verdict map; Durability is always ``"n/a"``.

    Each probe is isolated: a crashing probe yields ``"fail"`` for that property
    only, never propagating.
    """
    out: dict[str, str] = {}
    for name, fn in (("atomicity", check_atomicity), ("consistency", check_consistency)):
        try:
            out[name] = "pass" if fn(con) else "fail"
        except Exception:  # noqa: BLE001 - a broken probe is a fail, not a crash
            out[name] = "fail"
    try:
        iso = check_isolation(con, open_conn)
        out["isolation"] = "n/a" if iso is None else ("pass" if iso else "fail")
    except Exception:  # noqa: BLE001
        out["isolation"] = "fail"
    out["durability"] = "n/a"
    return out


# --- generic probes (engine table/column injected) -----------------------------


def _first_rowid(con: Any, table: str) -> int:
    return int(con.execute(f"SELECT min(rowid) FROM {table}").fetchone()[0])


def check_atomicity_generic(con: Any, *, table: str, value_column: str) -> bool:
    """Atomicity on any table: a rolled-back update leaves the row unchanged; a
    committed one applies (then is restored). Row addressed by DuckDB rowid."""
    rid = _first_rowid(con, table)
    read = f"SELECT {value_column} FROM {table} WHERE rowid = ?"  # noqa: S608 - identifiers are code-supplied
    v0 = con.execute(read, [rid]).fetchone()[0]
    con.execute("BEGIN TRANSACTION")
    con.execute(f"UPDATE {table} SET {value_column} = {value_column} + 1 WHERE rowid = ?", [rid])  # noqa: S608
    con.execute("ROLLBACK")
    rolled_back_ok = con.execute(read, [rid]).fetchone()[0] == v0

    con.execute("BEGIN TRANSACTION")
    con.execute(f"UPDATE {table} SET {value_column} = {value_column} + 1 WHERE rowid = ?", [rid])  # noqa: S608
    con.execute("COMMIT")
    committed_ok = con.execute(read, [rid]).fetchone()[0] != v0
    con.execute(f"UPDATE {table} SET {value_column} = ? WHERE rowid = ?", [v0, rid])  # noqa: S608
    restored_ok = con.execute(read, [rid]).fetchone()[0] == v0
    return rolled_back_ok and committed_ok and restored_ok


def check_isolation_generic(
    con: Any, open_conn: Callable[[], Any] | None, *, table: str, value_column: str
) -> bool | None:
    """Snapshot isolation on any table: an open reader keeps a stable view while a
    second connection commits an update. ``None`` when no second connection."""
    if open_conn is None:
        return None
    rid = _first_rowid(con, table)
    read = f"SELECT {value_column} FROM {table} WHERE rowid = ?"  # noqa: S608
    v0 = con.execute(read, [rid]).fetchone()[0]
    con.execute("BEGIN TRANSACTION")
    try:
        r1 = con.execute(read, [rid]).fetchone()[0]
        other = open_conn()
        try:
            other.execute(
                f"UPDATE {table} SET {value_column} = {value_column} + 1000 WHERE rowid = ?",  # noqa: S608
                [rid],
            )
        finally:
            other.close()
        r2 = con.execute(read, [rid]).fetchone()[0]
    finally:
        con.execute("ROLLBACK")
    con.execute(f"UPDATE {table} SET {value_column} = ? WHERE rowid = ?", [v0, rid])  # noqa: S608
    return r1 == r2


def run_acid_generic(
    con: Any,
    open_conn: Callable[[], Any] | None = None,
    *,
    table: str,
    value_column: str,
) -> dict[str, str]:
    """A/I probes on an injected table → verdict map; Consistency/Durability n/a.

    The consistency condition is suite-specific (TPC-H wires its order/lineitem
    invariant); a suite without one reports ``"n/a"`` honestly instead of
    inventing a check.
    """
    out: dict[str, str] = {}
    try:
        out["atomicity"] = (
            "pass" if check_atomicity_generic(con, table=table, value_column=value_column) else "fail"
        )
    except Exception:  # noqa: BLE001 - a broken probe is a fail, not a crash
        out["atomicity"] = "fail"
    out["consistency"] = "n/a"
    try:
        iso = check_isolation_generic(con, open_conn, table=table, value_column=value_column)
        out["isolation"] = "n/a" if iso is None else ("pass" if iso else "fail")
    except Exception:  # noqa: BLE001
        out["isolation"] = "fail"
    out["durability"] = "n/a"
    return out
