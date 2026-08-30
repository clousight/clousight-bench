import argparse
import json
from pathlib import Path

from clousight_bench.cli import _cmd_verify
from clousight_bench.core.fingerprints import record_digest


def _write_record(path: Path, measurements: dict, tamper: bool = False) -> None:
    payload = {
        "schema_version": "0.4",
        "run": {
            "run_id": "run-test",
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:00:01Z",
            "stages": {},
        },
        "identity": {
            "domain": "agent-runtime",
            "task_id": "stub.alt",
            "adapter": "local-sim",
            "task_revision": "1",
            "scorer_revision": "1",
            "core_version": "0.2.0",
            "adapter_status": "reference",
            "plugin_versions": {},
        },
        "environment": {
            "region": "",
            "mode": "local",
            "python_version": "3.12.0",
            "os_name": "Linux",
            "facts": {},
            "execution": "simulated",
        },
        "fingerprints": {"benchmark": "sha256:b", "environment": "sha256:e", "implementation": "sha256:i"},
        "measurements": {
            k: {"value": v, "unit": "ms", "reproducibility_class": "environmental"}
            for k, v in measurements.items()
        },
        "findings": [],
        "observations": {},
        "series": {},
        "artifacts": [],
        "extensions": {},
        "errors": [],
        "status": "completed",
    }
    payload["fingerprints"]["record_digest"] = record_digest(payload)
    if tamper:
        payload["measurements"]["lat"]["value"] = 9999
    path.write_text(json.dumps(payload), encoding="utf-8")


def _args(results_dir: str) -> argparse.Namespace:
    return argparse.Namespace(results=results_dir)


def test_all_pass_returns_0(tmp_path, capsys):
    domain = tmp_path / "agent-runtime" / "local-sim"
    domain.mkdir(parents=True)
    _write_record(domain / "stub.alt-run-abc.json", {"lat": 42.0})
    _write_record(domain / "stub.alt-run-def.json", {"lat": 43.0})
    rc = _cmd_verify(_args(str(tmp_path)))
    out = capsys.readouterr().out
    assert rc == 0
    assert "2 ok, 0 failed" in out
    assert "✓" in out


def test_tampered_file_returns_1(tmp_path, capsys):
    domain = tmp_path / "agent-runtime" / "local-sim"
    domain.mkdir(parents=True)
    _write_record(domain / "stub.alt-run-good.json", {"lat": 42.0})
    _write_record(domain / "stub.alt-run-bad.json", {"lat": 42.0}, tamper=True)
    rc = _cmd_verify(_args(str(tmp_path)))
    out = capsys.readouterr().out
    assert rc == 1
    assert "1 ok, 1 failed" in out
    assert "stub.alt-run-bad.json" in out
    assert "stored:" in out
    assert "computed:" in out


def test_unreadable_json_counts_as_failure(tmp_path, capsys):
    domain = tmp_path / "agent-runtime" / "local-sim"
    domain.mkdir(parents=True)
    (domain / "bad.json").write_text("not json", encoding="utf-8")
    rc = _cmd_verify(_args(str(tmp_path)))
    out = capsys.readouterr().out
    assert rc == 1
    assert "0 ok, 1 failed" in out


def test_aggregate_files_are_skipped_not_failed(tmp_path, capsys):
    agg_dir = tmp_path / "aggregates" / "agent-runtime" / "local-sim"
    agg_dir.mkdir(parents=True)
    (agg_dir / "stub.alt-plan-abc.json").write_text(
        json.dumps({"kind": "run_plan_aggregate"}), encoding="utf-8"
    )
    rc = _cmd_verify(_args(str(tmp_path)))
    out = capsys.readouterr().out
    assert rc == 0
    assert "0 ok, 0 failed" in out
    assert "skipped" in out


def test_empty_results_dir_returns_0(tmp_path, capsys):
    rc = _cmd_verify(_args(str(tmp_path)))
    out = capsys.readouterr().out
    assert rc == 0
    assert "0 ok, 0 failed" in out
