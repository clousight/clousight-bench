"""The progress plane must not leak the results directory's absolute path.

The viewer publishes only the results directory's basename (/api/meta says so
explicitly), so a record_path carrying the full filesystem path would hand the
same information back through a different door.
"""

from __future__ import annotations

import json
from pathlib import Path

from clousight_bench.core.orchestrator import _relative_record_path


def test_record_path_is_relative_to_the_results_dir(tmp_path: Path) -> None:
    results = tmp_path / "results"
    record = results / "data-warehouse" / "duckdb-local" / "suite:tpc-h-run-1.json"
    assert _relative_record_path(record, results) == "data-warehouse/duckdb-local/suite:tpc-h-run-1.json"


def test_a_relative_results_dir_still_yields_a_relative_record_path(tmp_path: Path, monkeypatch) -> None:
    """`csbench serve` defaults to the relative "results", while the store hands
    back an absolute path. Comparing the two unresolved raises, which degraded
    every record_path to a bare filename — losing the domain/platform prefix
    that makes it useful."""
    monkeypatch.chdir(tmp_path)
    record = (tmp_path / "results" / "data-warehouse" / "duckdb-local" / "suite:tpc-h-run-1.json").resolve()
    assert (
        _relative_record_path(record, Path("results")) == "data-warehouse/duckdb-local/suite:tpc-h-run-1.json"
    )


def test_a_record_outside_the_results_dir_is_named_but_not_located(tmp_path: Path) -> None:
    results = tmp_path / "results"
    elsewhere = tmp_path / "somewhere" / "else" / "run.json"
    assert _relative_record_path(elsewhere, results) == "run.json"


def test_a_traversal_run_id_cannot_name_a_progress_directory(tmp_path: Path) -> None:
    """``run_id`` arrives off an HTTP path on the progress and cancel routes.

    The token pattern alone is not enough: ".." matches ``[A-Za-z0-9._-]+``, and
    ``<results>/.progress/..`` is ``<results>`` — which would have let a cancel
    request create ``<results>/cancel`` and a stream read
    ``<results>/stream.jsonl``.
    """
    from clousight_bench.core.progress import (
        locate_progress_dir,
        progress_dir,
        progress_root,
        request_cancel,
        valid_run_id,
    )

    for hostile in ("..", ".", "../..", "..%2F..", "a/b", "/etc/passwd", "", "a b"):
        assert not valid_run_id(hostile), hostile
        assert progress_dir(tmp_path, hostile) is None, hostile
        assert locate_progress_dir(tmp_path, hostile) is None, hostile

    assert valid_run_id("run-20260907-000000-abcdef")
    ok = progress_dir(tmp_path, "run-20260907-000000-abcdef")
    assert ok is not None
    assert ok.parent == progress_root(tmp_path)

    # The one write the viewer can make refuses the same inputs, and refuses a
    # well-formed id that names nothing.
    assert request_cancel(tmp_path, "..") is False
    assert request_cancel(tmp_path, "run-does-not-exist") is False
    assert not (tmp_path / "cancel").exists()


def test_a_reader_never_joins_the_callers_string_into_a_path(tmp_path: Path) -> None:
    """The reader-side lookup returns a path the filesystem produced.

    Joining a caller's string and then checking the result is a pattern that has
    to be got exactly right every time; listing and matching cannot traverse at
    all, which is why load_record in the viewer works the same way.
    """
    from clousight_bench.core.progress import locate_progress_dir, progress_root

    root = progress_root(tmp_path)
    (root / "run-real").mkdir(parents=True)
    (root / "not-a-dir").parent.mkdir(parents=True, exist_ok=True)
    (root / "not-a-dir").write_text("", encoding="utf-8")

    found = locate_progress_dir(tmp_path, "run-real")
    assert found is not None
    assert found.name == "run-real"
    assert found.parent == root
    # A file by that name is not a progress directory.
    assert locate_progress_dir(tmp_path, "not-a-dir") is None
    assert locate_progress_dir(tmp_path, "run-absent") is None


def test_walkers_over_the_results_tree_skip_the_progress_plane(tmp_path: Path, capsys) -> None:
    """`Path.rglob` descends into hidden directories, so the dot convention is
    not self-enforcing — every walker has to ask.

    `csbench verify` did not: it reported a progress snapshot (which carries no
    record_digest) as a digest failure and exited non-zero over a file that was
    never a result.
    """
    import argparse

    from clousight_bench.cli.results import _cmd_verify
    from clousight_bench.core.fingerprints import record_digest
    from clousight_bench.core.progress import SCHEMA, progress_dir
    from clousight_bench.core.store import is_results_sidecar

    results = tmp_path / "results"
    out = results / "data-warehouse" / "duckdb-local"
    out.mkdir(parents=True)
    record: dict[str, object] = {
        "schema_version": "0.4",
        "status": "completed",
        "run": {"run_id": "run-1"},
        "fingerprints": {},
    }
    record["fingerprints"] = {"record_digest": record_digest(record)}
    (out / "suite:tpc-h-run-1.json").write_text(json.dumps(record), encoding="utf-8")

    live = progress_dir(results, "run-2")
    assert live is not None
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps({"schema": SCHEMA, "status": "completed"}), encoding="utf-8")
    (results / ".cost_ledger.json").write_text("{}", encoding="utf-8")

    assert is_results_sidecar(live / "state.json", results)
    assert is_results_sidecar(results / ".cost_ledger.json", results)
    assert not is_results_sidecar(out / "suite:tpc-h-run-1.json", results)

    # Raw evaluator output lives under artifacts/ and carries no record_digest
    # either. Verifying it made `csbench verify` exit 1 on a healthy results
    # directory as soon as any suite had run.
    art = results / "artifacts" / "suite-tpc-h-abc"
    art.mkdir(parents=True)
    (art / "summary.json").write_text(json.dumps({"queries": 22}), encoding="utf-8")
    (art / "answers.json").write_text("[1, 2, 3]", encoding="utf-8")

    assert _cmd_verify(argparse.Namespace(results=str(results))) == 0
    printed = capsys.readouterr().out
    assert "1 ok, 0 failed" in printed, printed
    assert "state.json" not in printed
    assert "summary.json" not in printed
