"""`csbench trace list` / `trace show` read and render the local execution
traces without any external tool."""

from clousight_bench.cli import main
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec
from clousight_bench.core.traceview import find_trace, render_show, trace_summaries


def _run(tmp_path):
    return execute(
        RunSpec("agent-runtime", "suite:stub.ok", "local-sim", target={"recovery": {"mode": "auto-retry"}}),
        results_dir=tmp_path,
    )


def test_summaries_and_lookup_by_run_or_trace_id(tmp_path):
    rec = _run(tmp_path)
    summaries = trace_summaries(tmp_path)
    assert len(summaries) == 1
    s = summaries[0]
    assert s["run_id"] == rec.run.run_id
    assert s["status"] == "completed"

    trace_id = rec.extensions["core"]["trace_id"]
    assert find_trace(tmp_path, rec.run.run_id) is not None
    assert find_trace(tmp_path, trace_id) is not None
    assert find_trace(tmp_path, "nope") is None


def test_render_show_is_a_stage_tree(tmp_path):
    _run(tmp_path)
    summaries = trace_summaries(tmp_path)
    spans = find_trace(tmp_path, summaries[0]["run_id"])
    out = render_show(spans)
    assert "csbench.run" in out
    assert "SETUP" in out and "TEARDOWN" in out
    assert "slowest" in out  # the slowest stage is flagged


def test_cli_trace_list_and_show(tmp_path, capsys):
    rec = _run(tmp_path)
    assert main(["trace", "list", "--results", str(tmp_path)]) == 0
    assert rec.run.run_id in capsys.readouterr().out

    assert main(["trace", "show", rec.run.run_id, "--results", str(tmp_path)]) == 0
    assert "csbench.run" in capsys.readouterr().out


def test_cli_trace_show_unknown_id_errors(tmp_path):
    _run(tmp_path)
    assert main(["trace", "show", "does-not-exist", "--results", str(tmp_path)]) == 2


# ---------------------------------------------------------------------------
# render_show: the tree, now that a benchmark's spans live in the run trace
# ---------------------------------------------------------------------------


def _merged_spans() -> list[dict[str, object]]:
    """A run trace shaped like one emit_run_trace writes since the merge."""

    def span(span_id, parent, name, ms, attrs=None, status="OK"):
        return {
            "trace_id": "t" * 32,
            "span_id": span_id,
            "parent_span_id": parent,
            "name": name,
            "start_unix_nano": 0,
            "end_unix_nano": int(ms * 1e6),
            "duration_ms": ms,
            "status": status,
            "attributes": attrs or {},
        }

    return [
        span("r", "", "csbench.run", 12064.0, {"csbench.run_id": "run-1", "csbench.status": "completed"}),
        span("p", "r", "csbench.stage.PREFLIGHT", 31.0, {"csbench.stage": "PREFLIGHT"}),
        span("e", "r", "csbench.stage.EXECUTE", 6979.0, {"csbench.stage": "EXECUTE"}),
        span("l", "e", "tpc-h.load", 6535.0, {"csbench.phase": "load"}),
        span("qs", "e", "tpc-h.query-set", 310.0, {"csbench.phase": "query-set"}),
        span("q1", "qs", "tpc-h.q1", 18.0, {"db.system": "duckdb"}),
        span("q2", "qs", "tpc-h.q2", 9.0, {"db.system": "duckdb"}, status="ERROR"),
    ]


def test_render_show_descends_into_the_benchmarks_own_spans() -> None:
    """Stopping at the stages would print strictly less than the trace holds,
    which is the thing this whole change was about."""
    out = render_show(_merged_spans())
    assert "csbench.run" in out
    assert "EXECUTE" in out
    assert "tpc-h.load" in out and "tpc-h.query-set" in out
    assert "31 spans" not in out  # the header counts what is there: 7
    assert "7 spans" in out


def test_render_show_counts_what_it_elides_rather_than_dropping_it() -> None:
    """A TPC official run is four deep and ~900 wide; a terminal is not a
    waterfall. What is cut must still be announced, or the tree ends in a lie."""
    out = render_show(_merged_spans(), depth=2)
    assert "tpc-h.q1" not in out
    assert "2 more span(s)" in out


def test_render_show_at_greater_depth_reaches_the_queries() -> None:
    out = render_show(_merged_spans(), depth=3)
    assert "tpc-h.q1" in out and "tpc-h.q2" in out
    assert "more span(s)" not in out


def test_render_show_flags_a_failure_at_any_depth() -> None:
    out = render_show(_merged_spans(), depth=3)
    failed = [line for line in out.splitlines() if "tpc-h.q2" in line]
    assert failed and "FAILED" in failed[0]


def test_render_show_survives_an_empty_trace() -> None:
    assert render_show([]) == "empty trace"
