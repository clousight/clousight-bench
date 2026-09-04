"""TPC-H refresh functions RF1 (New Sales) / RF2 (Old Sales) for DuckDB.

DuckDB's ``tpch`` extension provides dbgen + the 22 queries but NOT the refresh
functions, so these implement the spec's semantics and row counts with a
**clousight-generated update set** (folded into the ``unaudited`` provenance):

* ``rf1`` inserts ``N`` new orders (keys beyond the current max) plus their
  lineitems; each new order's ``o_totalprice`` is set to the lineitem-derived sum
  so the TPC-H consistency condition holds for generated rows too.
* ``rf2`` deletes the ``N`` oldest orders and their lineitems.

``N`` is ``round(SF * 1500)`` (spec clause 5.3.4), exposed via :func:`refresh_rows`.
The connection is passed in; this module imports no engine at module load.
"""

from __future__ import annotations

from typing import Any

_LINES_PER_ORDER = 4  # deterministic; spec allows 1..7 per order


def refresh_rows(scale_factor: float) -> int:
    """Number of orders inserted by RF1 / deleted by RF2 = round(SF * 1500), min 1."""
    return max(1, round(float(scale_factor) * 1500))


def rf1(con: Any, n_orders: int, *, lines_per_order: int = _LINES_PER_ORDER) -> None:
    """RF1 New Sales: insert *n_orders* new orders + their lineitems."""
    base = int(con.execute("SELECT COALESCE(max(o_orderkey), 0) FROM orders").fetchone()[0])
    max_part = int(con.execute("SELECT max(p_partkey) FROM part").fetchone()[0])
    max_supp = int(con.execute("SELECT max(s_suppkey) FROM supplier").fetchone()[0])
    n = int(n_orders)
    lpo = int(lines_per_order)

    con.execute(
        f"CREATE TEMP TABLE _rf1_new AS "
        f"SELECT ({base} + g) AS o_orderkey FROM generate_series(1, {n}) AS t(g)"
    )
    # lineitems first, so the order's totalprice can be derived from them
    con.execute(
        f"""
        INSERT INTO lineitem
        SELECT o.o_orderkey AS l_orderkey,
               ((o.o_orderkey * 7 + ln) % {max_part}) + 1 AS l_partkey,
               ((o.o_orderkey * 3 + ln) % {max_supp}) + 1 AS l_suppkey,
               ln AS l_linenumber,
               CAST(10 + ln AS DECIMAL(15,2)) AS l_quantity,
               CAST((10 + ln) * 100 AS DECIMAL(15,2)) AS l_extendedprice,
               CAST(0.05 AS DECIMAL(15,2)) AS l_discount,
               CAST(0.08 AS DECIMAL(15,2)) AS l_tax,
               'N' AS l_returnflag, 'O' AS l_linestatus,
               DATE '1998-01-01', DATE '1998-01-02', DATE '1998-01-03',
               'DELIVER IN PERSON' AS l_shipinstruct, 'TRUCK' AS l_shipmode,
               'rf1 generated' AS l_comment
        FROM _rf1_new o CROSS JOIN generate_series(1, {lpo}) AS l(ln)
        """
    )
    con.execute(
        """
        INSERT INTO orders
        SELECT o.o_orderkey,
               1 AS o_custkey,
               'O' AS o_orderstatus,
               COALESCE((SELECT CAST(SUM(l_extendedprice * (1 - l_discount) * (1 + l_tax)) AS DECIMAL(15,2))
                         FROM lineitem WHERE l_orderkey = o.o_orderkey), 0) AS o_totalprice,
               DATE '1998-01-01' AS o_orderdate,
               '1-URGENT' AS o_orderpriority,
               'Clerk#000000001' AS o_clerk,
               0 AS o_shippriority,
               'rf1 generated' AS o_comment
        FROM _rf1_new o
        """
    )
    con.execute("DROP TABLE _rf1_new")


def rf2(con: Any, n_orders: int) -> None:
    """RF2 Old Sales: delete the *n_orders* oldest orders and their lineitems."""
    n = int(n_orders)
    con.execute(f"CREATE TEMP TABLE _rf2_old AS SELECT o_orderkey FROM orders ORDER BY o_orderkey LIMIT {n}")
    con.execute("DELETE FROM lineitem WHERE l_orderkey IN (SELECT o_orderkey FROM _rf2_old)")
    con.execute("DELETE FROM orders WHERE o_orderkey IN (SELECT o_orderkey FROM _rf2_old)")
    con.execute("DROP TABLE _rf2_old")
