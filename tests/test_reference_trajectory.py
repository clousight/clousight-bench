"""The reference (non-official) paths seal the waterfall they already measured.

A suite that writes no ``trajectory.jsonl`` shows an empty ``EXECUTE`` in the run
trace — even when it measured 22 per-query intervals and drew every one of them
live. These tests pin the fix for the DuckDB TPC reference path: the same marks
that produced ``latency_ms`` and the live progress steps are laid out as v3
spans, named and nested exactly as the live plane named and nested them, and
nothing a measurement is built from moves.

Every assertion is about naming, nesting, span shape, or the ABSENCE of a second
timing. No assertion is about how long anything took.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from clousight_bench.core.suite import DriverContext, EnvHandle, RawArtifacts, Target
from clousight_bench.core.sut_span import is_v3_span, validate_span
from clousight_bench.suites.tpc_h.suite import TpchSuite

_DUCKDB = importlib.util.find_spec("duckdb") is not None
_needs_duckdb = pytest.mark.skipif(not _DUCKDB, reason="requires the [tpch] extra (duckdb)")

# Same knobs as tests/test_suite_progress.py: a real DuckDB run small enough for
# the fast gate, with enough queries that the per-query loop is a loop.
_SF = 0.01
_QUERIES = [1, 6, 14]
_TRACE_ID = "b" * 32


class RecordingProgress:
    """Records the live progress plane's calls; reports nowhere."""

    def __init__(self) -> None:
        self.steps: dict[str, tuple[str, float, float, str, str]] = {}

    def phase(self, label: str, total: int = 0, *, unit: str = "", reports_progress: bool = True) -> None:
        pass

    def advance(self, n: int = 1, *, label: str = "") -> None:
        pass

    def step(
        self, name: str, start_ms: float, end_ms: float, *, status: str = "ok", parent: str = ""
    ) -> None:
        self.steps[name] = (name, start_ms, end_ms, status, parent)

    def sample(self, key: str, value: float) -> None:
        pass

    def log(self, msg: str, *, level: str = "INFO") -> None:
        pass

    def should_cancel(self) -> bool:
        return False


def _real_target() -> Target:
    return Target(mode="runtime", mock=False)


def _reference_run(
    suite: Any = None, *, progress: RecordingProgress | None = None
) -> tuple[RawArtifacts, EnvHandle, Any]:
    """One real reference-mode run; the caller owns teardown of the returned env."""
    suite = suite if suite is not None else TpchSuite()
    driver = DriverContext(
        placement="local", trace_id=_TRACE_ID, **({"progress": progress} if progress else {})
    )
    dataset = suite.resolve({"scale_factor": _SF, "query_ids": _QUERIES}, None)
    env = suite.prepare(_real_target(), dataset, driver)
    return suite.run(_real_target(), env, driver), env, suite


def _spans(raw: RawArtifacts) -> list[dict[str, Any]]:
    return [json.loads(line) for line in raw.path("trajectory").read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# 1. the live waterfall and the sealed one describe the same run
# ---------------------------------------------------------------------------


@_needs_duckdb
def test_reference_run_seals_the_span_names_the_progress_plane_drew() -> None:
    rec = RecordingProgress()
    raw, env, suite = _reference_run(progress=rec)
    try:
        sealed = {s["name"] for s in _spans(raw)}
    finally:
        suite.teardown(env)

    assert sealed == set(rec.steps)
    assert sealed == {"tpc-h.load", "tpc-h.query-set", *(f"tpc-h.q{nr}" for nr in _QUERIES)}


@_needs_duckdb
def test_query_spans_nest_under_the_query_set_exactly_as_the_live_steps_do() -> None:
    rec = RecordingProgress()
    raw, env, suite = _reference_run(progress=rec)
    try:
        spans = _spans(raw)
    finally:
        suite.teardown(env)

    by_name = {s["name"]: s for s in spans}
    query_set = by_name["tpc-h.query-set"]
    assert query_set["parent_span_id"] == ""
    assert by_name["tpc-h.load"]["parent_span_id"] == ""
    for nr in _QUERIES:
        span = by_name[f"tpc-h.q{nr}"]
        assert span["parent_span_id"] == query_set["span_id"], span["name"]
        # ...and the live plane said the same thing, by name
        assert rec.steps[f"tpc-h.q{nr}"][4] == "tpc-h.query-set"
    assert rec.steps["tpc-h.query-set"][4] == ""


@_needs_duckdb
def test_the_trajectory_rides_the_runs_trace_id() -> None:
    raw, env, suite = _reference_run()
    try:
        spans = _spans(raw)
    finally:
        suite.teardown(env)
    assert spans and all(s["trace_id"] == _TRACE_ID for s in spans)


# ---------------------------------------------------------------------------
# 2. the spans are the intervals already measured — not a second timing
# ---------------------------------------------------------------------------


@_needs_duckdb
def test_each_query_span_is_exactly_the_interval_its_latency_ms_came_from() -> None:
    """A span's width IS the row's ``latency_ms``.

    Both are differences of the SAME two ``perf_counter`` marks, so they agree
    to the nanosecond quantisation of the wall-clock conversion (a couple of ns).
    A second, independent timing could not possibly land there.
    """
    raw, env, suite = _reference_run()
    try:
        spans = {s["name"]: s for s in _spans(raw)}
        rows = json.loads(raw.path("queries").read_text())
    finally:
        suite.teardown(env)

    assert rows
    for row in rows:
        span = spans[f"tpc-h.q{row['query_nr']}"]
        width_ns = span["end_unix_nano"] - span["start_unix_nano"]
        assert abs(width_ns - row["latency_ms"] * 1_000_000) <= 2, span["name"]
        assert span["attributes"]["csbench.row_count"] == row["row_count"]


@_needs_duckdb
def test_the_query_set_span_contains_every_query_span() -> None:
    """The set's own two marks bracket the loop, so every query lies inside it."""
    raw, env, suite = _reference_run()
    try:
        spans = {s["name"]: s for s in _spans(raw)}
    finally:
        suite.teardown(env)

    outer = spans["tpc-h.query-set"]
    for nr in _QUERIES:
        inner = spans[f"tpc-h.q{nr}"]
        assert outer["start_unix_nano"] <= inner["start_unix_nano"]
        assert inner["end_unix_nano"] <= outer["end_unix_nano"]
    # the load ran in prepare(), before the query set opened
    assert spans["tpc-h.load"]["end_unix_nano"] <= outer["start_unix_nano"]


# ---------------------------------------------------------------------------
# 3. the load-bearing one: no measurement moved
# ---------------------------------------------------------------------------


def _measurements(evaluator: Any, raw: RawArtifacts) -> str:
    return json.dumps(
        {k: dataclasses.asdict(v) for k, v in sorted(evaluator.evaluate(raw).items())}, sort_keys=True
    )


@_needs_duckdb
def test_measurements_are_byte_identical_with_and_without_the_trajectory() -> None:
    """Scoring one run's artifacts with the trajectory present and with it
    stripped must produce the same bytes.

    This is the whole safety claim: the trajectory is written after the last
    measured interval closed, out of a mark set the scored artifacts were
    already built from, and it is invisible to the evaluator. Comparing two
    *runs* could not prove it — wall-clock latencies differ between any two runs
    — so the comparison is made over one run's artifacts, which is exactly the
    input the evaluator is a pure function of.
    """
    from clousight_bench.suites.tpc_h.evaluator import OfficialTpchEvaluator

    raw, env, suite = _reference_run()
    try:
        assert "trajectory" in raw.manifest
        stripped = RawArtifacts(
            dir=raw.dir, manifest={k: v for k, v in raw.manifest.items() if k != "trajectory"}
        )
        evaluator = OfficialTpchEvaluator()
        assert _measurements(evaluator, raw) == _measurements(evaluator, stripped)
    finally:
        suite.teardown(env)


@_needs_duckdb
def test_the_scored_artifacts_are_untouched_by_the_trajectory() -> None:
    """queries.json / summary.json keep their exact manifest entries; the
    trajectory is an ADDITION, declared with its own sha256 and row count."""
    raw, env, suite = _reference_run()
    try:
        manifest = dict(raw.manifest)
        rows = json.loads(raw.path("queries").read_text())
        spans = _spans(raw)
    finally:
        suite.teardown(env)

    assert set(manifest) == {"queries", "summary", "trajectory"}
    assert manifest["queries"]["rows"] == len(rows) == len(_QUERIES)
    traj = manifest["trajectory"]
    assert traj["path"] == "trajectory.jsonl"
    assert traj["rows"] == len(spans)
    assert traj["sha256"].startswith("sha256:")


# ---------------------------------------------------------------------------
# 4. span shape
# ---------------------------------------------------------------------------


@_needs_duckdb
def test_every_emitted_span_validates_as_v3() -> None:
    raw, env, suite = _reference_run()
    try:
        spans = _spans(raw)
    finally:
        suite.teardown(env)

    assert spans
    for span in spans:
        assert is_v3_span(span), span["name"]
        validate_span(span)  # raises on anything the runner would reject


# ---------------------------------------------------------------------------
# 5. mock mode invents nothing
# ---------------------------------------------------------------------------


def test_mock_mode_writes_no_trajectory() -> None:
    """The bundled fixtures carry canned latencies nothing measured; drawing
    them as spans would put invented numbers on a waterfall."""
    from clousight_bench.suites.tpc_ds.suite import TpcdsSuite

    mock = Target(mode="runtime", mock=True)
    for suite in (TpchSuite(), TpcdsSuite()):
        driver = DriverContext(placement="local", trace_id=_TRACE_ID)
        env = suite.prepare(mock, suite.resolve({}, None), driver)
        raw = suite.run(mock, env, driver)
        assert "trajectory" not in raw.manifest, suite.suite_id
        assert not (raw.dir / "trajectory.jsonl").exists(), suite.suite_id


# ---------------------------------------------------------------------------
# 6. tpc-ds rides the same shared path
# ---------------------------------------------------------------------------


@pytest.mark.skipif(importlib.util.find_spec("duckdb") is None, reason="requires the [tpcds] extra (duckdb)")
def test_tpcds_reference_run_seals_the_same_shape() -> None:
    from clousight_bench.suites.tpc_ds.suite import TpcdsSuite

    rec = RecordingProgress()
    raw, env, suite = _reference_run(TpcdsSuite(), progress=rec)
    try:
        spans = _spans(raw)
    finally:
        suite.teardown(env)

    by_name = {s["name"]: s for s in spans}
    assert set(by_name) == set(rec.steps)
    assert "tpc-ds.query-set" in by_name
    for nr in _QUERIES:
        assert by_name[f"tpc-ds.q{nr}"]["parent_span_id"] == by_name["tpc-ds.query-set"]["span_id"]


# ---------------------------------------------------------------------------
# 7. end to end: the query spans land inside the run's EXECUTE stage
# ---------------------------------------------------------------------------


@_needs_duckdb
def test_execute_nests_the_query_spans_under_the_execute_stage(tmp_path, monkeypatch) -> None:
    """The point of the whole change: one tree, csbench.run → EXECUTE → q14."""
    from clousight_bench.core import orchestrator as orch
    from clousight_bench.core.schema import RunSpec

    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="data-warehouse",
        task_id="suite:tpc-h",
        platform="duckdb-local",
        target={"mode": "runtime"},
        params={"scale_factor": _SF, "query_ids": _QUERIES},
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=False)
    assert record.status == "completed", f"got {record.status}: {record.errors}"
    assert any(a.get("kind") == "trajectory" for a in record.artifacts), record.artifacts

    trace_id = record.extensions.get("core", {}).get("trace_id")
    assert trace_id
    spans = [
        json.loads(line)
        for line in Path(tmp_path / "traces" / f"{trace_id}.jsonl").read_text().splitlines()
        if line.strip()
    ]
    by_id = {s["span_id"]: s for s in spans}
    by_name = {s["name"]: s for s in spans}

    execute = by_name["csbench.stage.EXECUTE"]
    query_set = by_name["tpc-h.query-set"]
    assert by_id[query_set["parent_span_id"]] is execute
    for nr in _QUERIES:
        query = by_name[f"tpc-h.q{nr}"]
        assert by_id[query["parent_span_id"]] is query_set
        # a full chain, root to leaf, in one trace
        assert query["trace_id"] == by_name["csbench.run"]["trace_id"] == trace_id
    # the query windows really do fall inside the stage that ran them
    assert execute["start_unix_nano"] <= query_set["start_unix_nano"]
    assert query_set["end_unix_nano"] <= execute["end_unix_nano"]
