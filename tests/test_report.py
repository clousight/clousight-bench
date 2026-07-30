"""The report renders 0.2 measurements and derives red flags from findings."""
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.report import generate_report
from clousight_bench.core.schema import RunSpec


def test_empty_directory_says_so(tmp_path):
    report = generate_report(tmp_path)
    assert "No schema 0.2 results found" in report
    assert (tmp_path / "comparison.md").is_file()


def test_report_renders_measurements_with_evidence_and_status(tmp_path):
    execute(RunSpec("agent-runtime", "T1.3", "local-sim",
                    target={"recovery": {"mode": "auto-retry"}}), results_dir=tmp_path)
    report = generate_report(tmp_path)
    assert "## agent-runtime · T1.3" in report
    assert "recovery_mode=auto-retry [C]" in report
    assert "completed" in report
    assert "- none" in report


def test_warning_findings_become_red_flags(tmp_path):
    execute(RunSpec("agent-runtime", "T1.3", "local-sim",
                    target={"recovery": {"mode": "fail-fast"}}), results_dir=tmp_path)
    report = generate_report(tmp_path)
    assert "agent_runtime.recovery_fail_fast" in report
    assert "warning" in report


def test_report_surfaces_cost_column_and_partial_flag(tmp_path):
    # T5.1 emits usage measurements the pricing enricher prices; with only the
    # open-core seed prices, local's units are uncovered -> a $0 (partial) cell
    # plus a red flag naming what went unpriced.
    execute(RunSpec("agent-runtime", "T5.1", "local-sim"), results_dir=tmp_path)
    report = generate_report(tmp_path)
    assert "| adapter | status | measurements | cost | benchmark fingerprint | core |" in report
    assert "(partial)" in report
    assert "cost is partial" in report


def test_report_shows_dash_cost_for_non_usage_task(tmp_path):
    # T1.3 carries no usage measurements, so the enricher never prices it: the
    # cost cell stays a dash and no pricing red flag appears.
    execute(RunSpec("agent-runtime", "T1.3", "local-sim",
                    target={"recovery": {"mode": "auto-retry"}}), results_dir=tmp_path)
    report = generate_report(tmp_path)
    assert "| cost |" in report
    assert "USD" not in report
    assert "cost is partial" not in report


def test_unreadable_and_non_record_files_are_skipped(tmp_path):
    (tmp_path / "migration-manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "junk.json").write_text("not json", encoding="utf-8")
    assert "No schema 0.2 results found" in generate_report(tmp_path)
