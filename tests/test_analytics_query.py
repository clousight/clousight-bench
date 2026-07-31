import pytest

from clousight_bench.core.analytics import Analytics

pytest.importorskip("duckdb")


def test_query_measurements_join_records(tmp_path, write_record):
    write_record(tmp_path)
    a = Analytics(tmp_path)
    rows = a.query(
        "SELECT r.platform, m.name, m.value_num "
        "FROM measurements m JOIN records r USING (run_id) "
        "WHERE m.name = 'cold_start_ms'")
    assert rows == [{"platform": "local-sim", "name": "cold_start_ms", "value_num": 42.0}]


def test_query_cross_platform_aggregate(tmp_path, write_record):
    write_record(tmp_path, run_id="r1")
    rows = Analytics(tmp_path).query(
        "SELECT platform, count(*) n FROM records GROUP BY platform")
    assert rows == [{"platform": "local-sim", "n": 1}]


def test_query_empty_results_registers_typed_views(tmp_path):
    # No records: views still exist and are queryable, returning zero rows.
    rows = Analytics(tmp_path).query("SELECT * FROM measurements")
    assert rows == []
