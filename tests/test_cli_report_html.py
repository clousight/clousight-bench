from clousight_bench.cli import main
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec


def test_report_html_writes_self_contained_file(tmp_path):
    execute(RunSpec("agent-runtime", "T1.1", "local-sim"), results_dir=tmp_path)
    out = tmp_path / "report.html"
    rc = main(["report", "--results", str(tmp_path), "--format", "html", "--out", str(out)])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert html.lstrip().startswith("<!DOCTYPE html>") and "simulated" in html


def test_unknown_renderer_exit_2(tmp_path):
    execute(RunSpec("agent-runtime", "T1.1", "local-sim"), results_dir=tmp_path)
    rc = main(
        [
            "report",
            "--results",
            str(tmp_path),
            "--format",
            "html",
            "--renderer",
            "nope",
            "--out",
            str(tmp_path / "r.html"),
        ]
    )
    assert rc == 2
