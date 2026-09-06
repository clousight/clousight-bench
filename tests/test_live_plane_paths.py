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


def test_a_traversal_run_id_cannot_name_a_progress_directory(tmp_path: Path) -> None:
    """``run_id`` arrives off an HTTP path, so progress_dir is the containment
    boundary for the whole plane.

    The token pattern alone is not enough: ".." matches ``[A-Za-z0-9._-]+``, and
    ``<results>/.progress/..`` is ``<results>`` — which would have let a cancel
    request create ``<results>/cancel`` and a stream read
    ``<results>/stream.jsonl``.
    """
    from clousight_bench.core.progress import progress_dir, progress_root, request_cancel

    for hostile in ("..", ".", "../..", "..%2F..", "a/b", "/etc/passwd", "", "a b"):
        assert progress_dir(tmp_path, hostile) is None, hostile

    ok = progress_dir(tmp_path, "run-20260907-000000-abcdef")
    assert ok is not None
    assert ok.parent == progress_root(tmp_path)

    # And the one write the viewer can make refuses the same inputs.
    assert request_cancel(tmp_path, "..") is False
    assert not (tmp_path / "cancel").exists()
