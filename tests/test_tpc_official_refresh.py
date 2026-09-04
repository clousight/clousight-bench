"""RF1/RF2 refresh functions against a real (tiny-SF) DuckDB tpch database."""

from __future__ import annotations

import pytest

from clousight_bench.suites._tpc_official.refresh import refresh_rows, rf1, rf2

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("INSTALL tpch; LOAD tpch;")
    c.execute("CALL dbgen(sf := 0.01)")
    yield c
    c.close()


def _counts(con) -> tuple[int, int]:
    o = con.execute("SELECT count(*) FROM orders").fetchone()[0]
    lineitem = con.execute("SELECT count(*) FROM lineitem").fetchone()[0]
    return o, lineitem


def test_refresh_rows_is_round_sf_1500() -> None:
    assert refresh_rows(1.0) == 1500
    assert refresh_rows(0.01) == 15
    assert refresh_rows(0.0) == 1  # floored to at least one


def test_rf1_inserts_then_rf2_restores_order_count(con) -> None:
    o0, l0 = _counts(con)
    n = refresh_rows(0.01)

    rf1(con, n)
    o1, l1 = _counts(con)
    assert o1 == o0 + n
    assert l1 == l0 + n * 4  # 4 lines per new order

    rf2(con, n)
    o2, l2 = _counts(con)
    assert o2 == o0  # inserted n, deleted n -> order count restored
    assert l2 < l1  # oldest orders' lineitems removed


def test_rf1_generated_orders_satisfy_consistency_condition(con) -> None:
    n = refresh_rows(0.01)
    rf1(con, n)
    # every rf1-generated order's totalprice equals its lineitem-derived sum
    bad = con.execute(
        """
        SELECT count(*) FROM orders o
        WHERE o.o_comment = 'rf1 generated'
          AND o.o_totalprice <> COALESCE(
              (SELECT CAST(SUM(l_extendedprice*(1-l_discount)*(1+l_tax)) AS DECIMAL(15,2))
               FROM lineitem WHERE l_orderkey = o.o_orderkey), 0)
        """
    ).fetchone()[0]
    assert bad == 0
