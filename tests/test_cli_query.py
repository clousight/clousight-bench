import pytest

from clousight_bench.cli import main

pytest.importorskip("duckdb")


def test_cli_query_table_json(tmp_path, capsys, write_record):
    write_record(tmp_path)
    rc = main(["query", "--table", "records", "--results", str(tmp_path), "--format", "json"])
    out = capsys.readouterr().out
    assert rc == 0 and "local-sim" in out


def test_cli_export_csv(tmp_path, write_record):
    write_record(tmp_path)
    rc = main(
        [
            "export",
            "measurements",
            "--out",
            str(tmp_path / "m.csv"),
            "--format",
            "csv",
            "--results",
            str(tmp_path),
        ]
    )
    assert rc == 0 and (tmp_path / "m.csv").exists()


def test_cli_query_bad_sql_exit_2(tmp_path, write_record):
    write_record(tmp_path)
    rc = main(["query", "SELECT * FROM nope_table", "--results", str(tmp_path)])
    assert rc == 2
