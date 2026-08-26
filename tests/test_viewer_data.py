"""Tests for the read-only viewer reader layer (viewer/data.py).

The fixture results dir is REAL: it is produced once per module by running the
suite:swe-bench mock run through orchestrator.execute (same pattern as
tests/test_suite_bridge.py::test_bridge_end_to_end_mock_run), then copied per
test so mutations never leak between tests.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from clousight_bench.viewer.data import list_records, load_record, load_trajectory

SPAN_V2_KEYS = {"span_id", "trace_id", "parent_id", "name", "kind", "t_start", "t_end", "status"}


# ---------------------------------------------------------------------------
# Fixtures: one genuine orchestrator run, copied per test
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_results_dir(tmp_path_factory) -> Path:
    """Run the mock suite once and keep the resulting results_dir pristine."""
    import clousight_bench.core.orchestrator as orch
    from clousight_bench.core.schema import RunSpec

    base = tmp_path_factory.mktemp("viewer-results")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
        spec = RunSpec(
            domain="agent-runtime",
            task_id="suite:swe-bench",
            platform="local-sim",
            target={"mode": "mock"},
        )
        record = orch.execute(spec, results_dir=base, enrich=False, preflight=False)
    assert record.status == "completed", f"fixture run failed: {record.errors}"
    return base


@pytest.fixture()
def results_dir(real_results_dir: Path, tmp_path: Path) -> Path:
    """Per-test writable copy of the real results dir."""
    dest = tmp_path / "results"
    shutil.copytree(real_results_dir, dest)
    return dest


def _the_run_id(results_dir: Path) -> str:
    summaries = list_records(results_dir)
    assert len(summaries) == 1, f"expected exactly 1 record, got {len(summaries)}"
    return summaries[0]["run_id"]


# ---------------------------------------------------------------------------
# list_records
# ---------------------------------------------------------------------------


def test_list_records_single_real_run(results_dir: Path) -> None:
    summaries = list_records(results_dir)
    assert len(summaries) == 1
    s = summaries[0]
    assert s["status"] == "completed"
    assert s["domain"] == "agent-runtime"
    assert s["adapter"] == "local-sim"
    assert s["task_id"] == "suite:swe-bench"
    assert s["suite_id"] == "swe-bench"
    assert s["scaffold"] != ""
    assert s["has_trajectory"] is True
    assert s["started_at"]
    assert "swe-bench.resolved" in s["measurements"]
    # measurements are flattened to scalar values only
    assert isinstance(s["measurements"]["swe-bench.resolved"], float)


def test_list_records_skips_special_subtrees_and_dotfiles(results_dir: Path) -> None:
    """aggregates/, campaigns/, artifacts/, traces/, debug/ and dotfiles are not records."""
    decoy = {"run": {"run_id": "decoy"}, "identity": {}, "status": "completed"}
    for sub in ("aggregates", "campaigns", "artifacts", "traces", "debug"):
        d = results_dir / sub / "x"
        d.mkdir(parents=True, exist_ok=True)
        (d / "decoy.json").write_text(json.dumps(decoy))
    dot_dir = results_dir / "agent-runtime" / "local-sim"
    (dot_dir / ".hidden.json").write_text(json.dumps(decoy))
    hidden_domain = results_dir / ".secret" / "local-sim"
    hidden_domain.mkdir(parents=True)
    (hidden_domain / "decoy.json").write_text(json.dumps(decoy))

    summaries = list_records(results_dir)
    assert len(summaries) == 1
    assert summaries[0]["run_id"] != "decoy"


def test_list_records_skips_corrupt_file(results_dir: Path, caplog) -> None:
    """An unparseable JSON file in the tree is skipped with a warning, not fatal."""
    bad = results_dir / "agent-runtime" / "local-sim" / "corrupt-run.json"
    bad.write_text("{this is not json")
    with caplog.at_level("WARNING"):
        summaries = list_records(results_dir)
    assert len(summaries) == 1
    assert any("corrupt-run.json" in r.message for r in caplog.records)


def test_list_records_sorted_by_started_at_desc(results_dir: Path) -> None:
    newer = {
        "run": {"run_id": "run-newer", "started_at": "2099-01-01T00:00:00Z"},
        "identity": {"domain": "agent-runtime", "task_id": "t2", "adapter": "local-sim"},
        "status": "completed",
        "provenance": {},
        "measurements": {},
        "artifacts": [],
    }
    out = results_dir / "agent-runtime" / "local-sim" / "t2-run-newer.json"
    out.write_text(json.dumps(newer))
    summaries = list_records(results_dir)
    assert [s["run_id"] for s in summaries][0] == "run-newer"
    starts = [s["started_at"] for s in summaries]
    assert starts == sorted(starts, reverse=True)
    # hand-written record with empty provenance yields empty-string fields
    assert summaries[0]["suite_id"] == ""
    assert summaries[0]["scaffold"] == ""
    assert summaries[0]["has_trajectory"] is False


def test_list_records_missing_dir_is_empty(tmp_path: Path) -> None:
    assert list_records(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# load_record
# ---------------------------------------------------------------------------


def test_load_record_roundtrips(results_dir: Path) -> None:
    run_id = _the_run_id(results_dir)
    record = load_record(results_dir, run_id)
    assert record is not None
    assert record["run"]["run_id"] == run_id
    # It is the full on-disk dict, not a summary
    raw_files = [
        p
        for p in (results_dir / "agent-runtime" / "local-sim").glob("*.json")
        if p.name.endswith(f"-{run_id}.json")
    ]
    assert record == json.loads(raw_files[0].read_text())


def test_load_record_unknown_run_id(results_dir: Path) -> None:
    assert load_record(results_dir, "run-does-not-exist") is None


def test_load_record_rejects_traversal_run_id(results_dir: Path) -> None:
    assert load_record(results_dir, "../x") is None
    assert load_record(results_dir, "a/b") is None
    assert load_record(results_dir, "") is None


# ---------------------------------------------------------------------------
# load_trajectory
# ---------------------------------------------------------------------------


def test_load_trajectory_real_run(results_dir: Path) -> None:
    run_id = _the_run_id(results_dir)
    traj = load_trajectory(results_dir, run_id)
    assert traj is not None
    spans = traj["spans"]
    assert len(spans) == 3
    for span in spans:
        assert SPAN_V2_KEYS <= set(span), f"span missing v2 keys: {sorted(span)}"
    assert traj["t0"] == min(s["t_start"] for s in spans)


def test_load_trajectory_unknown_run_id(results_dir: Path) -> None:
    assert load_trajectory(results_dir, "run-does-not-exist") is None


def test_load_trajectory_rejects_traversal_run_id(results_dir: Path) -> None:
    assert load_trajectory(results_dir, "../x") is None


def test_load_trajectory_rejects_traversal_artifact_path(results_dir: Path) -> None:
    """A record whose trajectory artifact path escapes results_dir yields None."""
    evil = {
        "run": {"run_id": "run-evil", "started_at": "2026-01-01T00:00:00Z"},
        "identity": {"domain": "agent-runtime", "task_id": "t3", "adapter": "local-sim"},
        "status": "completed",
        "provenance": {},
        "measurements": {},
        "artifacts": [
            {
                "kind": "trajectory",
                "media": "application/jsonl",
                "sha256": "sha256:0",
                "path": "../../../../etc/passwd",
            }
        ],
    }
    out = results_dir / "agent-runtime" / "local-sim" / "t3-run-evil.json"
    out.write_text(json.dumps(evil))
    assert load_trajectory(results_dir, "run-evil") is None


def test_load_trajectory_traversal_never_reads_existing_outside_file(
    results_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Containment must refuse even when the escaping target EXISTS and is valid NDJSON.

    Mutation-killer: with the is_relative_to guard removed, the outside file would
    parse fine and spans would come back — this test then fails.
    """
    outside = results_dir.parent / "outside"
    outside.mkdir(exist_ok=True)
    span = {
        "span_id": "s1",
        "trace_id": "t1",
        "parent_id": None,
        "name": "leak",
        "kind": "tool_call",
        "t_start": 1.0,
        "t_end": 2.0,
        "status": "ok",
        "attrs": {},
    }
    (outside / "trajectory.jsonl").write_text(json.dumps(span) + "\n")
    evil = {
        "run": {"run_id": "run-evil2", "started_at": "2026-01-01T00:00:00Z"},
        "identity": {"domain": "agent-runtime", "task_id": "t4", "adapter": "local-sim"},
        "status": "completed",
        "provenance": {},
        "measurements": {},
        "artifacts": [
            {
                "kind": "trajectory",
                "media": "application/jsonl",
                "sha256": "sha256:0",
                "path": "../../outside/trajectory.jsonl",
            }
        ],
    }
    out = results_dir / "agent-runtime" / "local-sim" / "t4-run-evil2.json"
    out.write_text(json.dumps(evil))
    with caplog.at_level("WARNING"):
        assert load_trajectory(results_dir, "run-evil2") is None
    assert any("escapes results_dir" in r.message for r in caplog.records)


def test_load_trajectory_missing_file(results_dir: Path) -> None:
    """Record exists, artifact declared, but the file itself is gone -> None."""
    run_id = _the_run_id(results_dir)
    shutil.rmtree(results_dir / "artifacts")
    assert load_trajectory(results_dir, run_id) is None


def test_load_trajectory_no_trajectory_artifact(results_dir: Path) -> None:
    plain = {
        "run": {"run_id": "run-plain", "started_at": "2026-01-01T00:00:00Z"},
        "identity": {"domain": "agent-runtime", "task_id": "t4", "adapter": "local-sim"},
        "status": "completed",
        "provenance": {},
        "measurements": {},
        "artifacts": [],
    }
    out = results_dir / "agent-runtime" / "local-sim" / "t4-run-plain.json"
    out.write_text(json.dumps(plain))
    assert load_trajectory(results_dir, "run-plain") is None


def test_list_records_caches_unchanged_files(results_dir, monkeypatch):
    """Second call over an unchanged dir parses ZERO files (mtime/size cache)."""
    from clousight_bench.viewer import data as viewer_data

    viewer_data._SUMMARY_CACHE.clear()
    first = viewer_data.list_records(results_dir)
    assert first  # cache primed

    calls = []
    real = viewer_data._read_record

    def counting_read(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(viewer_data, "_read_record", counting_read)
    second = viewer_data.list_records(results_dir)
    assert second == first
    assert calls == [], f"unchanged files were re-parsed: {calls}"

    # A touched (rewritten) file re-parses exactly once.
    target = next(p for p in results_dir.rglob("*.json") if p.parent.name == "local-sim")
    target.write_text(target.read_text())  # new mtime/size identity
    viewer_data.list_records(results_dir)
    assert calls == [target]


def test_count_records_matches_list_without_parsing(results_dir, monkeypatch):
    from clousight_bench.viewer import data as viewer_data

    viewer_data._SUMMARY_CACHE.clear()
    expected = len(viewer_data.list_records(results_dir))

    def boom(path):  # count must never parse
        raise AssertionError(f"count_records parsed {path}")

    monkeypatch.setattr(viewer_data, "_read_record", boom)
    assert viewer_data.count_records(results_dir) == expected
