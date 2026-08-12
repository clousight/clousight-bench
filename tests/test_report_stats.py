"""The report aggregates real repeats, drops warmups, and flags numbers that
are not actually comparable."""

import json

from clousight_bench.core.fingerprints import record_digest
from clousight_bench.core.report import generate_report


def _record(
    run_id,
    *,
    benchmark="sha256:aaaaaaaaaaaaaa",
    implementation="sha256:c",
    value=10.0,
    run_plan=None,
    started_at="2026-07-26T00:00:00Z",
):
    payload = {
        "schema_version": "0.2",
        "run": {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": started_at,
            "stages": {"PERSIST": "ok"},
        },
        "identity": {
            "domain": "agent-runtime",
            "task_id": "T1.3",
            "task_revision": "2",
            "scorer_revision": "2",
            "adapter": "local-sim",
            "adapter_status": "reference",
            "core_version": "0.2.0",
        },
        "environment": {
            "region": "",
            "mode": "local",
            "python_version": "3.12.0",
            "os_name": "Linux",
            "facts": {},
        },
        "fingerprints": {
            "benchmark": benchmark,
            "environment": "sha256:b",
            "implementation": implementation,
            "record_digest": "",
        },
        "status": "completed",
        "measurements": {"p99_ms": {"value": value, "unit": "ms", "evidence": "C"}},
        "findings": [],
        "observations": {},
        "series": {},
        "artifacts": [],
        "extensions": {"core": {"run_plan": run_plan}} if run_plan else {},
        "errors": [],
    }
    payload["fingerprints"]["record_digest"] = record_digest(payload)
    return payload


def _write(directory, name, payload):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_a_single_run_has_no_statistics_section(tmp_path):
    _write(tmp_path, "one.json", _record("run-1"))
    report = generate_report(tmp_path)
    assert "## Repeated-run statistics" not in report  # the matrix already shows it


def test_repeats_are_pooled_into_a_distribution(tmp_path):
    for i, value in enumerate([10.0, 20.0, 30.0]):
        _write(tmp_path, f"r{i}.json", _record(f"run-{i}", value=value))
    report = generate_report(tmp_path)
    assert "## Repeated-run statistics" in report
    assert "p99_ms=20±10 ms (n=3, p95=30) [C]" in report


def test_warmup_records_are_excluded_from_the_statistics(tmp_path):
    _write(
        tmp_path,
        "w.json",
        _record("run-w", value=999.0, run_plan={"plan_id": "p", "role": "warmup", "index": 0}),
    )
    for i, value in enumerate([10.0, 20.0]):
        _write(
            tmp_path,
            f"m{i}.json",
            _record(f"run-m{i}", value=value, run_plan={"plan_id": "p", "role": "measured", "index": i}),
        )
    report = generate_report(tmp_path)
    # n counts only the two measured runs; the 999 warmup never moves the mean.
    assert "p99_ms=15±7.071 ms (n=2, p95=20) [C]" in report


def test_two_benchmarks_in_one_cell_are_flagged_as_incomparable(tmp_path):
    _write(tmp_path, "a.json", _record("run-a", benchmark="sha256:AAAAAAAAAAAAAA"))
    _write(tmp_path, "b.json", _record("run-b", benchmark="sha256:BBBBBBBBBBBBBB"))
    report = generate_report(tmp_path)
    assert "## Comparability" in report
    assert "different benchmarks and must not be compared" in report
    # Different benchmarks are never pooled into one distribution.
    assert "## Repeated-run statistics" not in report


def test_same_benchmark_different_code_is_flagged_as_a_caveat(tmp_path):
    _write(tmp_path, "a.json", _record("run-a", implementation="sha256:CODE1"))
    _write(tmp_path, "b.json", _record("run-b", implementation="sha256:CODE2"))
    report = generate_report(tmp_path)
    assert "## Comparability" in report
    assert "the code changed" in report


def test_a_persisted_aggregate_is_not_mistaken_for_a_record(tmp_path):
    _write(
        tmp_path,
        "aggregates/agent-runtime/local-sim/T1.3-plan-x.json",
        {"kind": "run_plan_aggregate", "schema_version": "0.2"},
    )
    _write(tmp_path, "one.json", _record("run-1"))
    report = generate_report(tmp_path)
    # The aggregate is skipped silently: it is not a 0.2 record and never warns.
    assert "T1.3-plan-x.json" not in report
