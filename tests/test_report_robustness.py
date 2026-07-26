"""A report must never crash on a results directory, and never skip in silence."""
import json

import pytest

from clousight_bench.core.report import generate_report

_LEGACY = {
    "schema_version": "1.0",
    "domain": "agent-runtime",
    "task_id": "T1.3",
    "platform": "local-sim",
    "run_id": "run-old",
    "ok": True,
    "metrics": {"p99_ms": 9},
}


def _record(run_id="run-1", started_at="2026-07-26T00:00:00Z", **overrides):
    payload = {
        "schema_version": "0.2",
        "run": {"run_id": run_id, "started_at": started_at,
                "finished_at": started_at, "stages": {"PERSIST": "ok"}},
        "identity": {"domain": "agent-runtime", "task_id": "T1.3", "task_revision": "2",
                     "scorer_revision": "2", "adapter": "local-sim",
                     "adapter_status": "reference", "core_version": "0.2.0"},
        "environment": {"region": "", "mode": "local", "python_version": "3.12.0",
                        "os_name": "Linux", "facts": {}},
        "fingerprints": {"benchmark": "sha256:aaaaaaaaaaaaaa", "environment": "sha256:b",
                         "implementation": "sha256:c", "record_digest": "sha256:d"},
        "status": "completed",
        "measurements": {"p99_ms": {"value": 9, "unit": "ms", "evidence": "C"}},
        "findings": [], "observations": {}, "series": {}, "artifacts": [],
        "extensions": {}, "errors": [],
    }
    payload.update(overrides)
    return payload


def _write(directory, name, payload):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_legacy_record_is_reported_with_a_migration_hint(tmp_path, capsys):
    _write(tmp_path, "old.json", _LEGACY)
    report = generate_report(tmp_path)
    err = capsys.readouterr().err

    assert "No schema 0.2 results found" in report
    assert "old.json" in err
    assert "migrate-results" in err
    assert "1 file" in err or "1 result file" in err


def test_unreadable_and_malformed_files_are_reported_not_swallowed(tmp_path, capsys):
    (tmp_path / "junk.json").write_text("not json", encoding="utf-8")
    _write(tmp_path, "list.json", [1, 2, 3])
    _write(tmp_path, "partial.json", {"schema_version": "0.2", "run": {}})
    (tmp_path / "directory.json").mkdir()

    report = generate_report(tmp_path)
    err = capsys.readouterr().err

    assert "No schema 0.2 results found" in report
    for name in ("junk.json", "list.json", "partial.json", "directory.json"):
        assert name in err


def test_a_good_record_next_to_a_broken_one_still_renders(tmp_path, capsys):
    _write(tmp_path, "good.json", _record())
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")

    report = generate_report(tmp_path)
    assert "p99_ms=9 ms [C]" in report
    assert "bad.json" in capsys.readouterr().err


def test_measurements_missing_optional_keys_do_not_crash_the_renderer(tmp_path):
    _write(tmp_path, "sparse.json", _record(
        measurements={"weird": {"value": 1}, "labelled": {"value": "x", "evidence": "B"}},
    ))
    report = generate_report(tmp_path)
    assert "weird=1" in report
    assert "labelled=x [B]" in report


def test_malformed_findings_do_not_crash_the_red_flag_list(tmp_path):
    _write(tmp_path, "f.json", _record(
        status="failed",
        errors=[{"stage": "EXECUTE"}],
        findings=[{"severity": "warning"}, "not-a-dict"],
    ))
    report = generate_report(tmp_path)
    assert "## Red flags" in report
    assert "status `failed`" in report


def test_persist_failure_is_flagged_even_if_status_is_completed(tmp_path):
    _write(
        tmp_path,
        "persist.json",
        _record(
            status="completed",
            run={
                "run_id": "run-1",
                "started_at": "2026-07-26T00:00:00Z",
                "finished_at": "2026-07-26T00:00:00Z",
                "stages": {"PERSIST": "failed"},
            },
        ),
    )
    report = generate_report(tmp_path)
    assert "PERSIST" in report
    assert "failed" in report


def test_any_recorded_error_is_a_red_flag_even_if_status_is_completed(tmp_path):
    _write(
        tmp_path,
        "teardown.json",
        _record(
            status="completed",
            errors=[
                {
                    "stage": "TEARDOWN",
                    "code": "teardown_failed",
                    "type": "OSError",
                    "message": "cleanup failed",
                    "retryable": True,
                }
            ],
        ),
    )
    report = generate_report(tmp_path)
    assert "TEARDOWN" in report
    assert "cleanup failed" in report


def test_ties_on_started_at_are_broken_deterministically(tmp_path):
    _write(tmp_path, "a.json", _record(run_id="run-a", measurements={
        "p99_ms": {"value": 1, "unit": "ms", "evidence": "C"}}))
    _write(tmp_path, "b.json", _record(run_id="run-b", measurements={
        "p99_ms": {"value": 2, "unit": "ms", "evidence": "C"}}))

    first = generate_report(tmp_path)
    second = generate_report(tmp_path)
    assert first == second
    assert "p99_ms=2 ms [C]" in first  # highest run_id wins a timestamp tie


@pytest.mark.skipif(
    not hasattr(__import__("os"), "geteuid") or __import__("os").geteuid() == 0,
    reason="root can read anything",
)
def test_an_unreadable_file_is_reported_and_skipped(tmp_path, capsys):
    path = _write(tmp_path, "locked.json", _record())
    path.chmod(0o000)
    try:
        generate_report(tmp_path)
        assert "locked.json" in capsys.readouterr().err
    finally:
        path.chmod(0o644)
