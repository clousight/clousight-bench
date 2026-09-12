"""The run trace is one tree, from ``csbench.run`` down to a single query.

Two halves used to exist and never meet. A suite gets the run's ``trace_id`` on
its ``DriverContext``, so its spans were always *the same trace* — but the
suite's root span had no parent, it lived in its own file, and the viewer showed
one file or the other. A reader of an official TPC run saw 76 query spans and no
lifecycle; a reader of a reference run saw the lifecycle and an empty EXECUTE.

They could not simply be merged, either: stages were laid end-to-end from their
durations, so the reconstructed EXECUTE window was wrong in absolute time and
the suite's spans (which carry real timestamps) fell outside it.

These tests pin both halves of the fix: real stage windows, and the suite's
spans nested under the stage that produced them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    Provenance,
    ResultRecord,
    RunInfo,
)
from clousight_bench.core.tracing import _in_parent_order, emit_run_trace

_TRACE_ID = "a" * 32
_MS = 1_000_000


def _record(*, stage_spans: dict[str, list[float]], artifacts: list[dict[str, Any]]) -> ResultRecord:
    return ResultRecord(
        run=RunInfo(
            run_id="run-1",
            started_at="2026-09-07T00:00:00Z",
            finished_at="2026-09-07T00:00:20Z",
            stages={"PREFLIGHT": "ok", "EXECUTE": "ok", "SCORE": "ok"},
            stage_timings={"PREFLIGHT": 30.0, "EXECUTE": 6000.0, "SCORE": 1.0},
            stage_spans=stage_spans,
        ),
        identity=Identity(
            domain="data-warehouse",
            task_id="suite:tpc-h",
            adapter="duckdb-local",
            task_revision="0",
            scorer_revision="0",
            adapter_status="reference",
            core_version="0.6.0",
        ),
        environment=Environment(region="", mode="local", python_version="3.12", os_name="Linux"),
        fingerprints=Fingerprints(benchmark="sha256:b", environment="sha256:e", implementation="sha256:i"),
        status="completed",
        provenance=Provenance(suite_id="tpc-h"),
        artifacts=artifacts,
    )


def _write_trajectory(results_dir: Path, subdir: str, spans: list[dict[str, Any]]) -> dict[str, Any]:
    out = results_dir / "artifacts" / subdir
    out.mkdir(parents=True)
    (out / "trajectory.jsonl").write_text("\n".join(json.dumps(s) for s in spans) + "\n", encoding="utf-8")
    return {"kind": "trajectory", "path": f"{subdir}/trajectory.jsonl", "media": "application/jsonl"}


def _emitted(results_dir: Path) -> list[dict[str, Any]]:
    path = results_dir / "traces" / f"{_TRACE_ID}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _suite_spans(root_start_ns: int) -> list[dict[str, Any]]:
    """A miniature official-mode trajectory: a root, a phase, two queries."""
    base = root_start_ns + 1000 * _MS  # 1s into the run — inside EXECUTE below
    return [
        {
            "trace_id": _TRACE_ID,
            "span_id": "1111111111111111",
            "parent_span_id": "",
            "name": "tpc-h.official",
            "start_unix_nano": base,
            "end_unix_nano": base + 4000 * _MS,
            "status": "OK",
            "attributes": {"csbench.phase": "official"},
        },
        {
            "trace_id": _TRACE_ID,
            "span_id": "2222222222222222",
            "parent_span_id": "1111111111111111",
            "name": "tpc-h.power",
            "start_unix_nano": base + 100 * _MS,
            "end_unix_nano": base + 3000 * _MS,
            "status": "OK",
            "attributes": {"csbench.phase": "power"},
        },
        {
            "trace_id": _TRACE_ID,
            "span_id": "3333333333333333",
            "parent_span_id": "2222222222222222",
            "name": "tpc-h.q1",
            "start_unix_nano": base + 150 * _MS,
            "end_unix_nano": base + 900 * _MS,
            "status": "OK",
            "attributes": {"db.system": "duckdb"},
        },
        {
            "trace_id": _TRACE_ID,
            "span_id": "4444444444444444",
            "parent_span_id": "2222222222222222",
            "name": "tpc-h.q6",
            "start_unix_nano": base + 950 * _MS,
            "end_unix_nano": base + 1500 * _MS,
            "status": "ERROR",
            "attributes": {"db.system": "duckdb"},
        },
    ]


@pytest.fixture()
def results_dir(tmp_path: Path) -> Path:
    return tmp_path


def test_the_suite_trajectory_nests_under_the_stage_that_produced_it(results_dir: Path) -> None:
    start_ns = 1_788_000_000_000_000_000
    artifact = _write_trajectory(results_dir, "suite-tpc-h-abc", _suite_spans(start_ns))
    record = _record(
        stage_spans={"PREFLIGHT": [0.0, 30.0], "EXECUTE": [500.0, 6500.0], "SCORE": [6600.0, 6601.0]},
        artifacts=[artifact],
    )
    emit_run_trace(record, results_dir, _TRACE_ID, start_ns, start_ns + 7000 * _MS)

    spans = _emitted(results_dir)
    by_id = {s["span_id"]: s for s in spans}
    names = {s["name"] for s in spans}
    assert {"csbench.run", "csbench.stage.EXECUTE", "tpc-h.official", "tpc-h.q1", "tpc-h.q6"} <= names

    execute = next(s for s in spans if s["name"] == "csbench.stage.EXECUTE")
    official = next(s for s in spans if s["name"] == "tpc-h.official")
    power = next(s for s in spans if s["name"] == "tpc-h.power")
    q1 = next(s for s in spans if s["name"] == "tpc-h.q1")

    # The suite's root hangs off EXECUTE; its own nesting is preserved beneath.
    assert official["parent_span_id"] == execute["span_id"]
    assert power["parent_span_id"] == official["span_id"]
    assert q1["parent_span_id"] == power["span_id"]
    # One tree: every span reaches csbench.run.
    root = next(s for s in spans if s["name"] == "csbench.run")
    for span in spans:
        hops, cursor = 0, span
        while cursor.get("parent_span_id") and hops < 10:
            cursor = by_id[cursor["parent_span_id"]]
            hops += 1
        assert cursor["span_id"] == root["span_id"], span["name"]


def test_the_suites_spans_fall_inside_the_stage_window(results_dir: Path) -> None:
    """The reason stage_spans had to exist.

    With stages laid end-to-end from durations, EXECUTE's reconstructed window
    was wrong in absolute time and the suite's real timestamps landed outside
    it — children drawn beyond their parent.
    """
    start_ns = 1_788_000_000_000_000_000
    artifact = _write_trajectory(results_dir, "suite-tpc-h-abc", _suite_spans(start_ns))
    record = _record(
        stage_spans={"PREFLIGHT": [0.0, 30.0], "EXECUTE": [500.0, 6500.0], "SCORE": [6600.0, 6601.0]},
        artifacts=[artifact],
    )
    emit_run_trace(record, results_dir, _TRACE_ID, start_ns, start_ns + 7000 * _MS)

    spans = _emitted(results_dir)
    execute = next(s for s in spans if s["name"] == "csbench.stage.EXECUTE")
    suite = [s for s in spans if s["name"].startswith("tpc-h.")]
    assert suite
    assert min(s["start_unix_nano"] for s in suite) >= execute["start_unix_nano"]
    assert max(s["end_unix_nano"] for s in suite) <= execute["end_unix_nano"]


def test_status_survives_the_replay(results_dir: Path) -> None:
    start_ns = 1_788_000_000_000_000_000
    artifact = _write_trajectory(results_dir, "suite-tpc-h-abc", _suite_spans(start_ns))
    record = _record(stage_spans={"EXECUTE": [500.0, 6500.0]}, artifacts=[artifact])
    emit_run_trace(record, results_dir, _TRACE_ID, start_ns, start_ns + 7000 * _MS)

    spans = {s["name"]: s for s in _emitted(results_dir)}
    assert spans["tpc-h.q6"]["status"] == "ERROR"
    assert spans["tpc-h.q1"]["status"] == "OK"


def test_a_record_without_stage_spans_still_renders(results_dir: Path) -> None:
    """Pre-0.6.1 records carry durations only. They fall back to the old
    end-to-end reconstruction — every duration right, every start time wrong —
    because a trace that will not render is worse than one that is imprecise."""
    start_ns = 1_788_000_000_000_000_000
    record = _record(stage_spans={}, artifacts=[])
    emit_run_trace(record, results_dir, _TRACE_ID, start_ns, start_ns + 7000 * _MS)

    spans = _emitted(results_dir)
    assert {s["name"] for s in spans} == {
        "csbench.run",
        "csbench.stage.PREFLIGHT",
        "csbench.stage.EXECUTE",
        "csbench.stage.SCORE",
    }
    # Laid end-to-end: PREFLIGHT's end is EXECUTE's start.
    preflight = next(s for s in spans if s["name"].endswith("PREFLIGHT"))
    execute = next(s for s in spans if s["name"].endswith("EXECUTE"))
    assert preflight["end_unix_nano"] == execute["start_unix_nano"]


def test_a_missing_or_unreadable_trajectory_never_breaks_the_trace(results_dir: Path) -> None:
    """Telemetry must not be the reason a run fails, so every unreadable input
    degrades to "the lifecycle, and nothing finer"."""
    start_ns = 1_788_000_000_000_000_000
    for artifacts in (
        [{"kind": "trajectory", "path": "does-not-exist/trajectory.jsonl"}],
        [{"kind": "trajectory", "path": "../../etc/passwd"}],
        [{"kind": "trajectory"}],  # no path at all
        [],
    ):
        (results_dir / "traces").mkdir(exist_ok=True)
        record = _record(stage_spans={"EXECUTE": [500.0, 6500.0]}, artifacts=list(artifacts))
        emit_run_trace(record, results_dir, _TRACE_ID, start_ns, start_ns + 7000 * _MS)
        names = {s["name"] for s in _emitted(results_dir)}
        assert names == {
            "csbench.run",
            "csbench.stage.PREFLIGHT",
            "csbench.stage.EXECUTE",
            "csbench.stage.SCORE",
        }


def test_a_garbled_trajectory_line_is_skipped_not_fatal(results_dir: Path) -> None:
    start_ns = 1_788_000_000_000_000_000
    spans = _suite_spans(start_ns)
    out = results_dir / "artifacts" / "suite-tpc-h-abc"
    out.mkdir(parents=True)
    (out / "trajectory.jsonl").write_text(
        json.dumps(spans[0]) + "\n{ not json\n" + json.dumps(spans[1]) + "\n", encoding="utf-8"
    )
    record = _record(
        stage_spans={"EXECUTE": [500.0, 6500.0]},
        artifacts=[{"kind": "trajectory", "path": "suite-tpc-h-abc/trajectory.jsonl"}],
    )
    emit_run_trace(record, results_dir, _TRACE_ID, start_ns, start_ns + 7000 * _MS)
    names = {s["name"] for s in _emitted(results_dir)}
    assert "tpc-h.official" in names and "tpc-h.power" in names


class TestParentOrdering:
    """A child emitted before its parent would silently re-root on the stage."""

    def test_children_follow_parents_however_the_file_is_ordered(self) -> None:
        spans = [
            {"span_id": "c", "parent_span_id": "b"},
            {"span_id": "b", "parent_span_id": "a"},
            {"span_id": "a", "parent_span_id": ""},
        ]
        assert [s["span_id"] for s in _in_parent_order(spans)] == ["a", "b", "c"]

    def test_a_dangling_parent_lands_on_the_stage(self) -> None:
        spans = [{"span_id": "orphan", "parent_span_id": "never-written"}]
        assert [s["span_id"] for s in _in_parent_order(spans)] == ["orphan"]

    def test_a_cycle_terminates(self) -> None:
        spans = [
            {"span_id": "x", "parent_span_id": "y"},
            {"span_id": "y", "parent_span_id": "x"},
        ]
        assert {s["span_id"] for s in _in_parent_order(spans)} == {"x", "y"}
