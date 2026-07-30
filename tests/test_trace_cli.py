"""`csbench trace list` / `trace show` read and render the local execution
traces without any external tool."""
from clousight_bench.cli import main
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec
from clousight_bench.core.traceview import find_trace, render_show, trace_summaries


def _run(tmp_path):
    return execute(
        RunSpec("agent-runtime", "T1.3", "local-sim",
                target={"recovery": {"mode": "auto-retry"}}),
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
