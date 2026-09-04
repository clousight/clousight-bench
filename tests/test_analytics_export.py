import csv
import json

import pytest

from clousight_bench.ops.analytics import Analytics


def test_export_csv(tmp_path, write_record):
    write_record(tmp_path)
    out = Analytics(tmp_path).export("measurements", tmp_path / "m.csv", fmt="csv")
    assert out.exists()
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert {r["name"] for r in rows} == {"cold_start_ms", "recovery_mode"}


def test_export_jsonl(tmp_path, write_record):
    write_record(tmp_path)
    out = Analytics(tmp_path).export("records", tmp_path / "r.jsonl", fmt="jsonl")
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1 and lines[0]["platform"] == "local-sim"


def test_export_unknown_format_rejected(tmp_path, write_record):
    write_record(tmp_path)
    with pytest.raises(ValueError):
        Analytics(tmp_path).export("records", tmp_path / "x.txt", fmt="txt")
