"""ACID probes against a real file-backed DuckDB tpch database."""

from __future__ import annotations

import os
import tempfile

import pytest

from clousight_bench.suites._tpc_official.acid import run_acid

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def db_path():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "t.duckdb")
    c = duckdb.connect(path)
    c.execute("INSTALL tpch; LOAD tpch;")
    c.execute("CALL dbgen(sf := 0.01)")
    c.close()
    yield path


def test_run_acid_all_pass_with_second_connection(db_path) -> None:
    con = duckdb.connect(db_path)
    out = run_acid(con, open_conn=lambda: duckdb.connect(db_path))
    con.close()
    assert out["atomicity"] == "pass"
    assert out["consistency"] == "pass"
    assert out["isolation"] == "pass"
    assert out["durability"] == "n/a"


def test_isolation_na_without_second_connection(db_path) -> None:
    con = duckdb.connect(db_path)
    out = run_acid(con, open_conn=None)
    con.close()
    assert out["isolation"] == "n/a"
    assert out["durability"] == "n/a"
