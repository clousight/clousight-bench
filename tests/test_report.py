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


def test_unreadable_and_non_record_files_are_skipped(tmp_path):
    (tmp_path / "migration-manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "junk.json").write_text("not json", encoding="utf-8")
    assert "No schema 0.2 results found" in generate_report(tmp_path)
