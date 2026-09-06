"""The progress plane must not leak the results directory's absolute path.

The viewer publishes only the results directory's basename (/api/meta says so
explicitly), so a record_path carrying the full filesystem path would hand the
same information back through a different door.
"""

from __future__ import annotations

from pathlib import Path

from clousight_bench.core.orchestrator import _relative_record_path


def test_record_path_is_relative_to_the_results_dir(tmp_path: Path) -> None:
    results = tmp_path / "results"
    record = results / "data-warehouse" / "duckdb-local" / "suite:tpc-h-run-1.json"
    assert _relative_record_path(record, results) == "data-warehouse/duckdb-local/suite:tpc-h-run-1.json"


def test_a_record_outside_the_results_dir_is_named_but_not_located(tmp_path: Path) -> None:
    results = tmp_path / "results"
    elsewhere = tmp_path / "somewhere" / "else" / "run.json"
    assert _relative_record_path(elsewhere, results) == "run.json"
