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

from clousight_bench.viewer.data import (
    list_records,
    load_board,
    load_record,
    load_suite,
    load_trajectory,
)

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
    # The merged trace: the lifecycle plus the suite's own spans. Asserting a
    # count would pin the number of stages a run happens to time, so this pins
    # the shape instead — both layers present, every span normalised.
    names = {s["name"] for s in spans}
    assert "csbench.run" in names
    assert any(not n.startswith("csbench.") for n in names)
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


def test_a_deleted_artifact_still_renders_from_the_merged_trace(results_dir: Path) -> None:
    """The sidecar's spans were re-emitted into the run trace at finalize, so
    losing the sidecar afterwards loses no spans.

    This deliberately relaxes an earlier rule ("never downgrade a declared
    artifact to the run trace"). That rule existed because the run trace was
    *thinner* — the lifecycle and nothing else. Now it is a superset, so the
    reason has dissolved. The next test pins the case where the rule still
    applies.
    """
    run_id = _the_run_id(results_dir)
    shutil.rmtree(results_dir / "artifacts")
    traj = load_trajectory(results_dir, run_id)
    assert traj is not None
    assert traj["source"] == "full"
    assert any(not s["name"].startswith("csbench.") for s in traj["spans"])


def test_a_pre_merge_record_with_a_deleted_artifact_is_still_none(results_dir: Path) -> None:
    """Where the run trace is lifecycle-only — a record written before the
    merge — a missing artifact must still be None rather than a silent
    downgrade to six stage bars the reader would mistake for the whole run."""
    run_id = _the_run_id(results_dir)
    record = json.loads(_record_path(results_dir, run_id).read_text())
    trace = results_dir / "traces" / f"{record['extensions']['core']['trace_id']}.jsonl"
    lifecycle_only = [
        line
        for line in trace.read_text().splitlines()
        if line.strip() and json.loads(line)["name"].startswith("csbench.")
    ]
    trace.write_text("\n".join(lifecycle_only) + "\n")
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


# ---------------------------------------------------------------------------
# load_trajectory: which of the two files a reader gets, and why
# ---------------------------------------------------------------------------


def _record_path(results_dir: Path, run_id: str) -> Path:
    paths = [p for p in (results_dir / "agent-runtime" / "local-sim").glob("*.json") if run_id in p.name]
    assert len(paths) == 1, paths
    return paths[0]


def test_load_trajectory_prefers_the_merged_run_trace(results_dir: Path) -> None:
    """The real run has BOTH a trajectory artifact and results/traces/<id>.jsonl.

    Since the merge, the run trace holds the suite's own spans re-emitted under
    the stage that produced them — so it is a superset, and preferring the
    sidecar would show a reader strictly less than exists.
    """
    run_id = _the_run_id(results_dir)
    record = json.loads(_record_path(results_dir, run_id).read_text())
    trace_id = record["extensions"]["core"]["trace_id"]
    assert (results_dir / "traces" / f"{trace_id}.jsonl").is_file()

    traj = load_trajectory(results_dir, run_id)
    assert traj is not None
    assert traj["source"] == "full"
    names = {span["name"] for span in traj["spans"]}
    assert "csbench.run" in names, "the lifecycle must be present"
    assert any(not n.startswith("csbench.") for n in names), "the suite's spans must be present"
    # A superset of the sidecar, which on its own carries 3 spans.
    assert len(traj["spans"]) > 3


def test_a_run_that_reported_nothing_finer_is_labelled_lifecycle(results_dir: Path) -> None:
    """Strip the suite's spans from the trace: what is left is the lifecycle,
    and the reader is told so rather than being left to wonder why the waterfall
    is six bars deep."""
    run_id = _the_run_id(results_dir)
    path = _record_path(results_dir, run_id)
    record = json.loads(path.read_text())
    record["artifacts"] = [a for a in record["artifacts"] if a.get("kind") != "trajectory"]
    path.write_text(json.dumps(record))
    trace = results_dir / "traces" / f"{record['extensions']['core']['trace_id']}.jsonl"
    lifecycle_only = [
        line
        for line in trace.read_text().splitlines()
        if line.strip() and json.loads(line)["name"].startswith("csbench.")
    ]
    trace.write_text("\n".join(lifecycle_only) + "\n")

    traj = load_trajectory(results_dir, run_id)
    assert traj is not None
    assert traj["source"] == "lifecycle"
    assert traj["spans"], "the run trace must produce spans"
    for span in traj["spans"]:
        assert SPAN_V2_KEYS <= set(span), f"span missing v2 keys: {sorted(span)}"
    # v3 spans are projected to seconds by _render_span, and t0 is their minimum
    assert traj["t0"] == min(s["t_start"] for s in traj["spans"])
    assert traj["t0"] > 0.0


def test_load_trajectory_without_artifact_or_trace_id_is_none(results_dir: Path) -> None:
    plain = {
        "run": {"run_id": "run-notrace", "started_at": "2026-01-01T00:00:00Z"},
        "identity": {"domain": "agent-runtime", "task_id": "t9", "adapter": "local-sim"},
        "status": "completed",
        "provenance": {},
        "measurements": {},
        "artifacts": [],
        "extensions": {},
    }
    out = results_dir / "agent-runtime" / "local-sim" / "t9-run-notrace.json"
    out.write_text(json.dumps(plain))
    assert load_trajectory(results_dir, "run-notrace") is None


def test_load_trajectory_run_trace_containment(results_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A trace_id shaped like a traversal must never read outside results_dir."""
    outside = results_dir.parent / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "leak.jsonl").write_text(json.dumps({"span_id": "x", "t_start": 1.0, "t_end": 2.0}) + "\n")
    evil = {
        "run": {"run_id": "run-tracevil", "started_at": "2026-01-01T00:00:00Z"},
        "identity": {"domain": "agent-runtime", "task_id": "t8", "adapter": "local-sim"},
        "status": "completed",
        "provenance": {},
        "measurements": {},
        "artifacts": [],
        "extensions": {"core": {"trace_id": "../../outside/leak"}},
    }
    out = results_dir / "agent-runtime" / "local-sim" / "t8-run-tracevil.json"
    out.write_text(json.dumps(evil))
    with caplog.at_level("WARNING"):
        assert load_trajectory(results_dir, "run-tracevil") is None
    # "/" is not a token character, so it never becomes a path at all
    assert not any("escapes results_dir" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Derived views over the real run
# ---------------------------------------------------------------------------


def test_load_board_over_the_real_run(results_dir: Path) -> None:
    board = load_board(results_dir)
    assert [d["domain"] for d in board["domains"]] == ["agent-runtime"]
    domain = board["domains"][0]
    assert domain["runs"] == 1
    suite = domain["suites"][0]
    assert suite["suite_id"] == "swe-bench"
    assert suite["task_id"] == "suite:swe-bench"
    assert suite["platforms"] == 1
    assert suite["latest"]["adapter"] == "local-sim"
    assert suite["latest"]["status"] == "completed"
    assert "swe-bench.resolved" in suite["latest"]["measurements"]


def test_load_suite_over_the_real_run(results_dir: Path) -> None:
    suite = load_suite(results_dir, "agent-runtime", "swe-bench")
    assert suite is not None
    assert suite["metric_keys"] == sorted(suite["metric_keys"])
    assert "swe-bench.resolved" in suite["metric_keys"]
    platform = suite["platforms"][0]
    assert platform["adapter"] == "local-sim"
    assert platform["runs"] == 1
    assert platform["history"] == [
        {
            "run_id": platform["latest"]["run_id"],
            "started_at": platform["latest"]["started_at"],
            "status": platform["latest"]["status"],
            "measurements": platform["latest"]["measurements"],
        }
    ]
    assert platform["latest"]["suite_version"] != ""


def test_load_suite_rejects_non_token_segments(results_dir: Path) -> None:
    for domain, suite_id in (("../etc", "swe-bench"), ("agent-runtime", "../x"), ("", "swe-bench")):
        assert load_suite(results_dir, domain, suite_id) is None


def test_board_skips_records_it_cannot_place(tmp_path: Path) -> None:
    """A schema 0.1-era record has no ``identity`` block, so it summarises to
    empty strings. Bucketing those under ("", "") put a nameless, valueless card
    on the board — indistinguishable, to a reader, from a bug."""
    legacy = tmp_path / "agent-runtime" / "local-sim"
    legacy.mkdir(parents=True)
    (legacy / "T1.3-run-old.json").write_text(
        json.dumps(
            {  # flat 0.1 shape: no identity, no run, no provenance, no measurements
                "domain": "agent-runtime",
                "task_id": "T1.3",
                "platform": "local-sim",
                "run_id": "run-old",
            }
        ),
        encoding="utf-8",
    )
    modern = tmp_path / "data-warehouse" / "duckdb-local"
    modern.mkdir(parents=True)
    (modern / "suite:tpc-h-run-new.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "run": {"run_id": "run-new", "started_at": "2026-09-06T00:00:00Z"},
                "identity": {"domain": "data-warehouse", "task_id": "suite:tpc-h", "adapter": "duckdb-local"},
                "provenance": {"suite_id": "tpc-h"},
                "measurements": {"tpc-h.queries_passed": {"value": 1.0}},
            }
        ),
        encoding="utf-8",
    )

    board = load_board(tmp_path)
    assert [domain["domain"] for domain in board["domains"]] == ["data-warehouse"]
    # Still listed by /api/records — skipped from the board, not hidden. Its
    # fields summarise to blanks (a 0.1 record has no `run` block to read a
    # run_id out of), which is precisely why it cannot be placed on a board.
    rows = list_records(tmp_path)
    assert len(rows) == 2
    assert sorted(row["run_id"] for row in rows) == ["", "run-new"]
