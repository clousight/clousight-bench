"""The report renders 0.2 measurements and derives red flags from findings."""

from clousight_bench.core.orchestrator import execute
from clousight_bench.core.report import generate_report
from clousight_bench.core.schema import RunSpec


def test_empty_directory_says_so(tmp_path):
    report = generate_report(tmp_path)
    assert "No schema 0.2 results found" in report
    assert (tmp_path / "comparison.md").is_file()


def test_report_renders_measurements_with_evidence_and_status(tmp_path):
    execute(
        RunSpec("agent-runtime", "T1.3", "local-sim", target={"recovery": {"mode": "auto-retry"}}),
        results_dir=tmp_path,
    )
    report = generate_report(tmp_path)
    assert "## agent-runtime · T1.3" in report
    # New shape: recovered=True [B] and observed_attempts [B]
    assert "recovered=True [B]" in report
    assert "observed_attempts=" in report
    assert "- none" in report


def test_warning_findings_become_red_flags(tmp_path):
    # platform_terminated=True generates agent_runtime.platform_timeout_recovery finding.
    # Monkeypatch the local-sim probe_fault_recovery to return platform_terminated=True.
    from unittest.mock import patch

    from clousight_bench.domains.agent_runtime.adapters.base import FaultRecoveryResult
    from clousight_bench.domains.agent_runtime.adapters.transport import MockRuntimeTransport

    def _terminated(self):
        return FaultRecoveryResult(
            recovered=False,
            observed_attempts=1,
            recovery_ms=5000.0,
            platform_terminated=True,
        )

    with patch.object(MockRuntimeTransport, "probe_fault_recovery", _terminated):
        execute(
            RunSpec("agent-runtime", "T1.3", "local-sim"),
            results_dir=tmp_path,
        )
    report = generate_report(tmp_path)
    assert "agent_runtime.platform_timeout_recovery" in report
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
    execute(
        RunSpec("agent-runtime", "T1.3", "local-sim", target={"recovery": {"mode": "auto-retry"}}),
        results_dir=tmp_path,
    )
    report = generate_report(tmp_path)
    assert "| cost |" in report
    assert "USD" not in report
    assert "cost is partial" not in report


def test_cost_summary_totals_across_runs_with_detail(tmp_path):
    # Two priced runs -> a campaign total, per-adapter runs, per-unit detail, and
    # a note about unpriced usage. local-sim T5.1 emits usage the seed can't price
    # (partial), so it exercises the uncovered path across repeats.
    execute(RunSpec("agent-runtime", "T5.1", "local-sim"), results_dir=tmp_path)
    execute(RunSpec("agent-runtime", "T5.1", "local-sim"), results_dir=tmp_path)
    report = generate_report(tmp_path)
    assert "## Cost summary" in report
    assert "Total:" in report
    assert "| adapter | runs | cost |" in report
    assert "Unpriced usage seen" in report


def test_no_cost_summary_when_nothing_priced(tmp_path):
    # A task with no usage measurements -> no pricing extension -> no cost summary.
    execute(
        RunSpec("agent-runtime", "T1.3", "local-sim", target={"recovery": {"mode": "auto-retry"}}),
        results_dir=tmp_path,
    )
    report = generate_report(tmp_path)
    assert "## Cost summary" not in report


def test_unreadable_and_non_record_files_are_skipped(tmp_path):
    (tmp_path / "migration-manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "junk.json").write_text("not json", encoding="utf-8")
    assert "No schema 0.2 results found" in generate_report(tmp_path)


def test_load_series_reads_flat_and_nested(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from clousight_bench.core.report import _load_series

    def write(p, task):
        pq.write_table(
            pa.table(
                {
                    "task_id": [task, task],
                    "series": ["curve_ms", "curve_ms"],
                    "t": [1, 2],
                    "value": [87000.0, 70.0],
                    "unit": ["", ""],
                }
            ),
            p,
        )

    write(tmp_path / "T1.13.series.parquet", "T1.13")
    nested = tmp_path / "agent-runtime" / "x" / "run-1"
    nested.mkdir(parents=True)
    write(nested / "series.parquet", "T0.1")

    got = _load_series(tmp_path)
    assert got["T1.13"]["curve_ms"] == [
        {"t": 1, "value": 87000.0, "unit": ""},
        {"t": 2, "value": 70.0, "unit": ""},
    ]
    assert "T0.1" in got
