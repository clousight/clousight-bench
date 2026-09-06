"""What the benchmark suites report to the progress plane while they run.

Every assertion here is about the SHAPE of the reporting — which phases, which
step names, how many advances, does a cancel stop the loop — never about a
timing, because a timing is exactly the thing this work is forbidden to move.

The load-bearing test is the last one: with a reporter attached, a suite must
produce the same artifacts it produces with the inert default. Progress is
telemetry; it may not touch a result.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from typing import Any

import pytest

from clousight_bench.core.errors import RunCancelled
from clousight_bench.core.suite import DriverContext, EnvHandle, Target
from clousight_bench.suites.tpc_h.suite import TpchSuite

_DUCKDB = importlib.util.find_spec("duckdb") is not None
_needs_duckdb = pytest.mark.skipif(not _DUCKDB, reason="requires the [tpch] extra (duckdb)")

# Small enough that a real DuckDB run stays inside the fast gate, big enough that
# a per-query loop is genuinely a loop.
_SF = 0.01
_QUERIES = [1, 6, 14]


class RecordingProgress:
    """A ``ProgressReporter`` that records calls and reports nowhere.

    ``cancel`` makes ``should_cancel()`` answer True, which is how a run is
    interrupted from the viewer.
    """

    def __init__(self, *, cancel: bool = False) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        #: (label, total, unit, reports_progress) per phase() call.
        self.phases: list[tuple[str, int, str, bool]] = []
        self.cancel = cancel

    # --- the six-method suite-facing surface --------------------------------

    def phase(self, label: str, total: int = 0, *, unit: str = "", reports_progress: bool = True) -> None:
        self.calls.append(("phase", (label, total, unit)))
        self.phases.append((label, total, unit, reports_progress))

    def advance(self, n: int = 1, *, label: str = "") -> None:
        self.calls.append(("advance", (n, label)))

    def step(
        self, name: str, start_ms: float, end_ms: float, *, status: str = "ok", parent: str = ""
    ) -> None:
        self.calls.append(("step", (name, start_ms, end_ms, status, parent)))

    def sample(self, key: str, value: float) -> None:
        self.calls.append(("sample", (key, value)))

    def log(self, msg: str, *, level: str = "INFO") -> None:
        self.calls.append(("log", (msg, level)))

    def should_cancel(self) -> bool:
        self.calls.append(("should_cancel", ()))
        return self.cancel

    # --- reading it back -----------------------------------------------------

    def of(self, kind: str) -> list[tuple[Any, ...]]:
        return [args for k, args in self.calls if k == kind]

    def after_phase(self, label: str) -> RecordingProgress:
        """A view of everything reported from the named phase onwards."""
        for i, (kind, args) in enumerate(self.calls):
            if kind == "phase" and args[0] == label:
                sliced = RecordingProgress()
                sliced.calls = self.calls[i:]
                return sliced
        raise AssertionError(f"no phase {label!r} in {[a for k, a in self.calls if k == 'phase']}")

    def steps(self) -> dict[str, tuple[Any, ...]]:
        return {args[0]: args for args in self.of("step")}


def _real_target() -> Target:
    return Target(mode="runtime", mock=False)


# ----------------------------------------------------------------------------
# 1. the plain (reference-mode) path
# ----------------------------------------------------------------------------


@_needs_duckdb
def test_plain_path_announces_the_query_set_and_advances_once_per_query() -> None:
    suite = TpchSuite()
    rec = RecordingProgress()
    driver = DriverContext(placement="local", progress=rec)
    dataset = suite.resolve({"scale_factor": _SF, "query_ids": _QUERIES}, None)
    env = suite.prepare(_real_target(), dataset, driver)
    try:
        suite.run(_real_target(), env, driver)
    finally:
        suite.teardown(env)

    # the data load is its own phase, announced before it happens
    assert ("Load", 1, "dataset") in rec.of("phase")
    # the query set is announced with its true size, in queries
    assert ("Query set", len(_QUERIES), "query") in rec.of("phase")
    # ...and advanced exactly once per query (the Load phase's single advance
    # belongs to the phase before it)
    assert rec.after_phase("Query set").of("advance") == [(1, "")] * len(_QUERIES)


@_needs_duckdb
def test_plain_path_steps_are_named_and_nested_like_the_trajectory() -> None:
    suite = TpchSuite()
    rec = RecordingProgress()
    driver = DriverContext(placement="local", progress=rec)
    dataset = suite.resolve({"scale_factor": _SF, "query_ids": _QUERIES}, None)
    env = suite.prepare(_real_target(), dataset, driver)
    try:
        raw = suite.run(_real_target(), env, driver)
    finally:
        suite.teardown(env)

    steps = rec.steps()
    assert set(steps) == {"tpc-h.load", "tpc-h.query-set", *(f"tpc-h.q{nr}" for nr in _QUERIES)}
    for nr in _QUERIES:
        name, start_ms, end_ms, status, parent = steps[f"tpc-h.q{nr}"]
        assert parent == "tpc-h.query-set", name
        assert status == "ok"
        assert 0.0 <= start_ms <= end_ms
    # every step lives in one frame: the load starts it, the query set follows it
    assert steps["tpc-h.load"][1] < steps["tpc-h.query-set"][1]

    # a sample per query, and it carries the SAME number the artifact does —
    # a sample is a copy of a measured value, never a second measurement
    reported = [value for key, value in rec.of("sample") if key == "tpc-h.latency_ms"]
    measured = [row["latency_ms"] for row in json.loads(raw.path("queries").read_text())]
    assert reported == measured


@_needs_duckdb
def test_cancel_stops_the_query_loop_early_and_produces_no_result() -> None:
    suite = TpchSuite()
    rec = RecordingProgress(cancel=True)
    driver = DriverContext(placement="local", progress=rec)
    dataset = suite.resolve({"scale_factor": _SF, "query_ids": list(range(1, 23))}, None)
    env = suite.prepare(_real_target(), dataset, driver)
    try:
        with pytest.raises(RunCancelled):
            suite.run(_real_target(), env, driver)
    finally:
        suite.teardown(env)
    # it stopped at the first query instead of grinding through all 22 — and it
    # RAISED, so a truncated query set can never be sealed as a finished run
    assert len(rec.of("advance")) < 22


# ----------------------------------------------------------------------------
# 2. official mode — the phase machine shared with tpc-ds
# ----------------------------------------------------------------------------


@_needs_duckdb
def test_official_mode_reports_the_official_phases_with_trajectory_span_names() -> None:
    suite = TpchSuite()
    rec = RecordingProgress()
    driver = DriverContext(placement="local", progress=rec)
    cfg = {"mode": "official", "scale_factor": _SF, "streams": 2, "query_ids": [1, 6]}
    dataset = suite.resolve(cfg, None)
    env = suite.prepare(_real_target(), dataset, driver)
    try:
        raw = suite.run(_real_target(), env, driver)
    finally:
        suite.teardown(env)

    labels = [args[0] for args in rec.of("phase")]
    assert labels == ["Load", "Power", "Throughput Test", "ACID"]

    # Every step the live view drew must exist, by name, in the sealed
    # trajectory — the two waterfalls have to be talking about the same thing.
    sealed = {
        json.loads(line)["name"] for line in raw.path("trajectory").read_text().splitlines() if line.strip()
    }
    live = set(rec.steps())
    assert live <= sealed, sorted(live - sealed)
    assert {
        "tpc-h.official",
        "tpc-h.load",
        "tpc-h.power",
        "tpc-h.rf1",
        "tpc-h.rf2",
        "tpc-h.q1",
        "tpc-h.throughput",
        "tpc-h.stream1",
        "tpc-h.s1.q1",
        "tpc-h.refresh-pair1",
    } <= live

    # the throughput block is replayed from its own measured intervals, so its
    # steps nest exactly as the reconstruction does
    steps = rec.steps()
    assert steps["tpc-h.s1.q1"][4] == "tpc-h.stream1"
    assert steps["tpc-h.stream1"][4] == "tpc-h.throughput"
    assert steps["tpc-h.throughput"][4] == "tpc-h.official"
    assert steps["tpc-h.q1"][4] == "tpc-h.power"


@_needs_duckdb
def test_official_mode_cancel_aborts_before_a_result_exists() -> None:
    suite = TpchSuite()
    rec = RecordingProgress(cancel=True)
    driver = DriverContext(placement="local", progress=rec)
    cfg = {"mode": "official", "scale_factor": _SF, "streams": 2}
    dataset = suite.resolve(cfg, None)
    env = suite.prepare(_real_target(), dataset, driver)
    try:
        with pytest.raises(RunCancelled):
            suite.run(_real_target(), env, driver)
    finally:
        suite.teardown(env)
    # the cancel is seen at the top of run(), so the expensive pipeline (Power,
    # Throughput, ACID) never starts at all — only prepare()'s Load was announced
    assert [args[0] for args in rec.of("phase")] == ["Load"]
    assert set(rec.steps()) == {"tpc-h.load"}


@_needs_duckdb
def test_tpcds_official_shape_reports_all_seven_phases() -> None:
    from clousight_bench.suites.tpc_ds.suite import TpcdsSuite

    suite = TpcdsSuite()
    rec = RecordingProgress()
    driver = DriverContext(placement="local", progress=rec)
    cfg = {"mode": "official", "scale_factor": _SF, "streams": 2, "query_ids": [1, 3]}
    dataset = suite.resolve(cfg, None)
    env = suite.prepare(_real_target(), dataset, driver)
    try:
        raw = suite.run(_real_target(), env, driver)
    finally:
        suite.teardown(env)

    assert [args[0] for args in rec.of("phase")] == [
        "Load",
        "Power",
        "Throughput Test 1",
        "Data Maintenance 1",
        "Throughput Test 2",
        "Data Maintenance 2",
        "ACID",
    ]
    sealed = {
        json.loads(line)["name"] for line in raw.path("trajectory").read_text().splitlines() if line.strip()
    }
    live = set(rec.steps())
    assert live <= sealed, sorted(live - sealed)
    assert {"tpc-ds.dm1", "tpc-ds.dm2", "tpc-ds.throughput1.stream1", "tpc-ds.throughput2"} <= live


def test_throughput_streams_poll_for_cancel_per_stream() -> None:
    """The cancel check inside the Throughput test has to fire per stream.

    Each stream is its own worker, so a poll that only ran on the driving thread
    would leave S long-running streams grinding on after a cancel.
    """
    from clousight_bench.suites._tpc_official.streams import run_throughput

    polled: list[int] = []

    def poll() -> None:
        polled.append(1)
        if len(polled) > 3:
            raise RunCancelled("stop")

    with pytest.raises(RunCancelled):
        run_throughput(
            [[1, 2, 3], [3, 2, 1], [2, 1, 3]],
            lambda sid, nr: {"query_nr": nr, "interval_s": 0.001, "row_count": 0, "result_digest": ""},
            None,
            poll=poll,
        )


# ----------------------------------------------------------------------------
# 3. the subprocess-wrapping suites
# ----------------------------------------------------------------------------


def _fake_completed(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["ycsb"], returncode=0, stdout=stdout, stderr="")


def _ycsb_env() -> EnvHandle:
    return EnvHandle(
        {
            "mock": False,
            "binary": "/usr/bin/false",  # never executed: subprocess.run is faked
            "binding": "basic",
            "props": [],
            "workload": "workloada",
            "recordcount": 100,
            "operationcount": 100,
            "reliability": None,
            "endpoint": "",
        }
    )


def test_ycsb_reports_its_two_phases_with_trajectory_span_names(monkeypatch) -> None:
    from clousight_bench.suites.ycsb import suite as ycsb_suite

    monkeypatch.setattr(ycsb_suite.subprocess, "run", lambda *a, **k: _fake_completed("[OVERALL], x, 1"))
    rec = RecordingProgress()
    driver = DriverContext(placement="local", progress=rec)
    raw = ycsb_suite.YcsbSuite().run(_real_target(), _ycsb_env(), driver)

    assert [args[0] for args in rec.of("phase")] == ["Load", "Run"]
    assert rec.of("advance") == [(1, ""), (1, "")]
    sealed = {
        json.loads(line)["name"] for line in raw.path("trajectory").read_text().splitlines() if line.strip()
    }
    assert set(rec.steps()) == {"ycsb.load", "ycsb.run"} == sealed


def test_ycsb_cancel_lands_before_the_measured_run_phase(monkeypatch) -> None:
    from clousight_bench.suites.ycsb import suite as ycsb_suite

    calls: list[str] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        calls.append(cmd[1])
        return _fake_completed()

    monkeypatch.setattr(ycsb_suite.subprocess, "run", fake_run)
    rec = RecordingProgress(cancel=True)
    driver = DriverContext(placement="local", progress=rec)
    with pytest.raises(RunCancelled):
        ycsb_suite.YcsbSuite().run(_real_target(), _ycsb_env(), driver)
    assert calls == []  # not even the load phase was started


def test_tpcc_reports_one_phase_named_like_its_span(monkeypatch) -> None:
    from clousight_bench.suites.tpc_c import suite as tpcc_suite

    summary_src = tpcc_suite._FIXTURES_DIR / "mock" / "summary.json"

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        # BenchBase writes its summary into -d <results_dir>; fake that.
        results_dir = cmd[cmd.index("-d") + 1]
        (tpcc_suite.Path(results_dir) / "01.summary.json").write_text(summary_src.read_text())
        return _fake_completed()

    monkeypatch.setattr(tpcc_suite.subprocess, "run", fake_run)
    rec = RecordingProgress()
    driver = DriverContext(placement="local", progress=rec)
    env = EnvHandle(
        {
            "mock": False,
            "launcher": "/usr/bin/false",
            "dbtype": "sqlite",
            "endpoint": "",
            "scalefactor": 1,
            "terminals": 1,
            "time": 1,
        }
    )
    raw = tpcc_suite.TpccSuite().run(_real_target(), env, driver)

    assert [args[0] for args in rec.of("phase")] == ["BenchBase create+load+execute"]
    sealed = {
        json.loads(line)["name"] for line in raw.path("trajectory").read_text().splitlines() if line.strip()
    }
    assert set(rec.steps()) == {"tpc-c.benchbase"} == sealed


# ----------------------------------------------------------------------------
# 4. the llm suites (mmlu / gsm8k / human-eval share llm_common's ItemProgress)
# ----------------------------------------------------------------------------


def _mmlu_env(suite, count: int) -> EnvHandle:
    questions = suite.resolve({"limit": count}, None).payload["questions"]
    return EnvHandle(
        {
            "mock": False,
            "endpoint": "https://llm.example.com/v1",
            "model": "test-model",
            "api_key": "",
            "questions": questions,
        }
    )


def test_mmlu_reports_one_step_and_one_sample_per_question(monkeypatch) -> None:
    from clousight_bench.suites.mmlu import suite as mmlu_suite

    monkeypatch.setattr(
        mmlu_suite, "chat_once", lambda **kwargs: ("A", {"prompt_tokens": 1, "completion_tokens": 1}, "stop")
    )
    suite = mmlu_suite.MmluSuite()
    env = _mmlu_env(suite, 3)
    rec = RecordingProgress()
    raw = suite.run(_real_target(), env, DriverContext(placement="local", progress=rec))

    assert rec.of("phase") == [("Questions", 3, "question")]
    assert len(rec.of("advance")) == 3
    answers = json.loads(raw.path("answers").read_text())
    assert set(rec.steps()) == {f"mmlu.{row['id']}" for row in answers}
    assert [key for key, _ in rec.of("sample")] == ["mmlu.latency_ms"] * 3


def test_mmlu_cancel_stops_after_the_first_question(monkeypatch) -> None:
    from clousight_bench.suites.mmlu import suite as mmlu_suite

    asked: list[int] = []

    def fake_chat(**kwargs):  # noqa: ARG001
        asked.append(1)
        return "A", {}, "stop"

    monkeypatch.setattr(mmlu_suite, "chat_once", fake_chat)
    suite = mmlu_suite.MmluSuite()
    env = _mmlu_env(suite, 5)
    rec = RecordingProgress(cancel=True)
    with pytest.raises(RunCancelled):
        suite.run(_real_target(), env, DriverContext(placement="local", progress=rec))
    assert len(asked) == 1


# ----------------------------------------------------------------------------
# 5. the point of all of it: reporting is inert
# ----------------------------------------------------------------------------


def _artifact_bytes(raw) -> dict[str, bytes]:
    return {name: raw.path(name).read_bytes() for name in raw.manifest}


def test_mock_paths_are_byte_identical_with_and_without_a_reporter() -> None:
    """``mode: mock`` must not notice the progress plane at all."""
    from clousight_bench.suites.tpc_c.suite import TpccSuite
    from clousight_bench.suites.tpc_ds.suite import TpcdsSuite
    from clousight_bench.suites.ycsb.suite import YcsbSuite

    mock = Target(mode="runtime", mock=True)
    for suite, cfg in (
        (TpchSuite(), {}),
        (TpchSuite(), {"mode": "official"}),
        (TpcdsSuite(), {}),
        (YcsbSuite(), {}),
        (TpccSuite(), {}),
    ):
        plain = suite.prepare(mock, suite.resolve(cfg, None), DriverContext(placement="local"))
        rec = RecordingProgress()
        watched = suite.prepare(
            mock, suite.resolve(cfg, None), DriverContext(placement="local", progress=rec)
        )
        a = _artifact_bytes(suite.run(mock, plain, DriverContext(placement="local")))
        b = _artifact_bytes(suite.run(mock, watched, DriverContext(placement="local", progress=rec)))
        assert a == b, f"{suite.suite_id} {cfg}"
        assert rec.calls == [], f"{suite.suite_id} {cfg} reported from a mock run"


@_needs_duckdb
def test_a_watched_real_run_produces_the_same_artifacts_as_an_unwatched_one() -> None:
    """The only artifact difference a reporter may cause is none.

    Wall-clock latencies are environmental and differ between any two runs, so
    they are compared for presence and shape; everything a digest, a fingerprint
    or a score is built from is compared byte for byte.
    """
    cfg = {"scale_factor": _SF, "query_ids": _QUERIES}

    def _run(driver: DriverContext) -> dict[str, Any]:
        suite = TpchSuite()
        env = suite.prepare(_real_target(), suite.resolve(cfg, None), driver)
        try:
            raw = suite.run(_real_target(), env, driver)
            return {
                "queries": json.loads(raw.path("queries").read_text()),
                "summary": raw.path("summary").read_bytes(),
                "manifest": {k: (v["path"], v["rows"]) for k, v in raw.manifest.items()},
            }
        finally:
            suite.teardown(env)

    quiet = _run(DriverContext(placement="local"))  # NULL_PROGRESS, the default
    watched = _run(DriverContext(placement="local", progress=RecordingProgress()))

    assert quiet["summary"] == watched["summary"]
    assert quiet["manifest"] == watched["manifest"]
    for a, b in zip(quiet["queries"], watched["queries"], strict=True):
        assert a["query_nr"] == b["query_nr"]
        assert a["row_count"] == b["row_count"]
        assert a["result_digest"] == b["result_digest"]
        assert a.keys() == b.keys()
        assert a["latency_ms"] > 0 and b["latency_ms"] > 0


@_needs_duckdb
def test_measured_windows_declare_that_they_cannot_report_progress() -> None:
    """A phase whose own wall clock IS the metric cannot tick through itself.

    The TPC throughput tests, data maintenance, the dataset load and the
    single-opaque-process phases all announce a total they will never advance
    through, because writing inside the window would perturb the number being
    measured. They must say so: a bar pinned at 0/396 for the longest phase of a
    run reads as a hang, and the viewer has no other way to tell the difference.
    """
    suite = TpchSuite()
    rec = RecordingProgress()
    driver = DriverContext(placement="local", progress=rec)
    dataset = suite.resolve({"scale_factor": _SF, "query_ids": _QUERIES}, None)
    env = suite.prepare(_real_target(), dataset, driver)
    try:
        suite.run(_real_target(), env, driver)
    finally:
        suite.teardown(env)

    reports = {label: flag for label, _total, _unit, flag in rec.phases}
    assert reports["Load"] is False, "the dataset load is one opaque call"
    assert reports["Query set"] is True, "per-query reporting lands between measured intervals"


@_needs_duckdb
def test_the_official_throughput_window_declares_itself_unreportable() -> None:
    """The Throughput test's elapsed_s is Throughput@Size — nothing may be
    written inside it, so it must not present a bar it will never move."""
    suite = TpchSuite()
    rec = RecordingProgress()
    driver = DriverContext(placement="local", progress=rec)
    cfg = {"mode": "official", "scale_factor": _SF, "streams": 2, "query_ids": [1, 6]}
    dataset = suite.resolve(cfg, None)
    env = suite.prepare(_real_target(), dataset, driver)
    try:
        suite.run(_real_target(), env, driver)
    finally:
        suite.teardown(env)

    reports = {label: flag for label, _total, _unit, flag in rec.phases}
    assert reports["Throughput Test"] is False
    assert reports["Power"] is True, "power sums per-query intervals; reporting fits in the gaps"
