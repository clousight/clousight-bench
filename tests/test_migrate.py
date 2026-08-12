"""Migration is non-destructive, deterministic, idempotent and lossless."""

import hashlib
import json
from pathlib import Path

import pytest

from clousight_bench.cli import main
from clousight_bench.core.fingerprints import record_digest
from clousight_bench.core.migrate import (
    MANIFEST_FILE,
    MigrationError,
    migrate_record,
    migrate_tree,
)
from clousight_bench.core.record import ResultRecord
from clousight_bench.core.redaction import REDACTED

_LEGACY = {
    "domain": "agent-runtime",
    "task_id": "T1.3",
    "platform": "local-sim",
    "run_id": "run-20260101-000000-aaaaaa",
    "started_at": "2026-01-01T00:00:00Z",
    "finished_at": "2026-01-01T00:00:05Z",
    "config_hash": "sha256:0123456789abcdef",
    "evidence_layer": "C",
    "metrics": {"recovery_mode": "auto-retry", "time_to_recovery_ms": 12.5},
    "ok": True,
    "runner_version": "1.0.0",
    "raw": {"attempts": [{"ok": True}]},
    "notes": "fault on call #[3]",
    "schema_version": "1.0",
    "series": {"latency_ms": [[1, 10.0]]},
    "artifacts": [
        {
            "kind": "trace",
            "path": "t.json",
            "media": "application/json",
            "sha256": "sha256:aa",
        }
    ],
    "error": None,
}


def _write(directory: Path, name: str, payload: object) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_migrate_record_produces_a_valid_0_2_payload():
    out = migrate_record(_LEGACY, source_path="a/b.json", source_sha256="ff")
    assert out["schema_version"] == "0.2"
    assert out["status"] == "completed"
    assert out["identity"]["domain"] == "agent-runtime"
    assert out["identity"]["adapter"] == "local-sim"
    assert out["identity"]["core_version"] == "1.0.0"
    assert out["run"]["run_id"] == "run-20260101-000000-aaaaaa"
    assert ResultRecord.from_dict(out).to_dict() == out
    assert record_digest(out) == out["fingerprints"]["record_digest"]


def test_metrics_become_measurements_carrying_the_legacy_evidence_layer():
    out = migrate_record(_LEGACY, source_path="a", source_sha256="ff")
    assert out["measurements"]["recovery_mode"] == {
        "value": "auto-retry",
        "unit": "",
        "evidence": "C",
        "notes": "migrated from schema 1.0 metrics",
    }
    assert out["measurements"]["time_to_recovery_ms"]["value"] == 12.5


def test_unknown_fingerprints_are_the_literal_unknown_never_fabricated():
    fingerprints = migrate_record(_LEGACY, source_path="a", source_sha256="ff")["fingerprints"]
    assert fingerprints["benchmark"] == "unknown"
    assert fingerprints["environment"] == "unknown"
    assert fingerprints["implementation"] == "unknown"
    assert fingerprints["record_digest"].startswith("sha256:")


def test_unrecorded_environment_declines_to_guess_local_or_cloud():
    environment = migrate_record(_LEGACY, source_path="a", source_sha256="ff")["environment"]
    assert environment["mode"] == "unknown"
    assert environment["region"] == "unknown"


def test_legacy_only_fields_land_in_extensions_legacy():
    legacy = migrate_record(_LEGACY, source_path="a/b.json", source_sha256="ff")["extensions"]["legacy"]
    assert legacy["config_hash"] == "sha256:0123456789abcdef"
    assert legacy["evidence_layer"] == "C"
    assert legacy["ok"] is True
    assert legacy["notes"] == "fault on call #[3]"
    assert legacy["source_path"] == "a/b.json"
    assert legacy["source_sha256"] == "ff"


def test_raw_series_and_artifacts_are_never_lost():
    out = migrate_record(_LEGACY, source_path="a", source_sha256="ff")
    assert out["observations"]["legacy_raw"] == {"attempts": [{"ok": True}]}
    assert out["series"] == {"latency_ms": [[1, 10.0]]}
    assert out["artifacts"][0]["sha256"] == "sha256:aa"


def test_sensitive_legacy_values_are_redacted_but_source_is_not_mutated():
    source = {
        **_LEGACY,
        "raw": {"api_token": "secret-token", "nested": {"password": "secret"}},
    }
    out = migrate_record(source, source_path="a", source_sha256="ff")
    assert out["observations"]["legacy_raw"]["api_token"] == REDACTED
    assert out["observations"]["legacy_raw"]["nested"]["password"] == REDACTED
    assert source["raw"]["api_token"] == "secret-token"


def test_failed_legacy_runs_map_to_the_correct_stage_errors():
    failed = migrate_record(
        {**_LEGACY, "ok": False, "error": "ConnectionError: dropped"},
        source_path="a",
        source_sha256="ff",
    )
    assert failed["status"] == "failed"
    assert failed["errors"] == [
        {
            "stage": "EXECUTE",
            "code": "legacy_error",
            "type": "LegacyError",
            "message": "ConnectionError: dropped",
            "retryable": False,
        }
    ]

    invalid = migrate_record(
        {
            **_LEGACY,
            "ok": False,
            "error": "preflight failed: credentials",
            "metrics": {"preflight_ok": False},
        },
        source_path="a",
        source_sha256="ff",
    )
    assert invalid["status"] == "invalid"
    assert invalid["errors"][0]["stage"] == "PREFLIGHT"
    assert invalid["errors"][0]["code"] == "legacy_preflight_failed"


def test_missing_legacy_error_uses_a_deterministic_message():
    out = migrate_record(
        {**_LEGACY, "ok": False, "error": None},
        source_path="a",
        source_sha256="ff",
    )
    assert out["errors"][0]["message"] == ("legacy run reported ok=false without an error message")


def test_migrate_tree_refuses_unsafe_or_existing_destinations(tmp_path):
    source = tmp_path / "old"
    _write(source, "result.json", _LEGACY)
    with pytest.raises(MigrationError, match="in place"):
        migrate_tree(source, source)
    with pytest.raises(MigrationError, match="inside"):
        migrate_tree(source, source / "out")

    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "keep"
    marker.write_text("untouched", encoding="utf-8")
    with pytest.raises(MigrationError, match="already exists"):
        migrate_tree(source, existing)
    assert marker.read_text(encoding="utf-8") == "untouched"


def test_migrate_tree_preserves_layout_source_and_manifest(tmp_path):
    source = tmp_path / "old"
    dest = tmp_path / "new"
    legacy_path = _write(source, "agent-runtime/local-sim/T1.3-run-1.json", _LEGACY)
    original = legacy_path.read_bytes()
    manifest = migrate_tree(source, dest)

    migrated = dest / "agent-runtime" / "local-sim" / "T1.3-run-1.json"
    assert migrated.is_file()
    assert json.loads(migrated.read_text())["schema_version"] == "0.2"
    assert legacy_path.read_bytes() == original
    assert manifest.migrated == 1 and manifest.skipped == 0
    assert manifest.failed == 0
    written = json.loads((dest / MANIFEST_FILE).read_text(encoding="utf-8"))
    entry = written["entries"][0]
    assert entry["source"] == "agent-runtime/local-sim/T1.3-run-1.json"
    assert entry["source_sha256"] == _sha(legacy_path)
    assert entry["status"] == "migrated"


def test_migration_is_deterministic_byte_for_byte(tmp_path):
    source = tmp_path / "old"
    _write(source, "a/T1.3-run-1.json", _LEGACY)
    first = tmp_path / "n1"
    second = tmp_path / "n2"
    migrate_tree(source, first)
    migrate_tree(source, second)
    assert (first / "a/T1.3-run-1.json").read_bytes() == (second / "a/T1.3-run-1.json").read_bytes()
    assert (first / MANIFEST_FILE).read_bytes() == (second / MANIFEST_FILE).read_bytes()


def test_already_migrated_and_bad_files_are_reported_individually(tmp_path):
    source = tmp_path / "old"
    dest = tmp_path / "new"
    _write(source, "already.json", {"schema_version": "0.2"})
    _write(source, "array.json", [])
    (source / "broken.json").write_text("{not json", encoding="utf-8")
    manifest = migrate_tree(source, dest)
    statuses = {entry.source: entry.status for entry in manifest.entries}
    assert statuses == {
        "already.json": "skipped",
        "array.json": "failed",
        "broken.json": "failed",
    }
    assert manifest.migrated == 0 and manifest.skipped == 1
    assert manifest.failed == 2


def test_symlinked_source_files_are_failed_without_reading_outside_tree(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    outside = _write(tmp_path, "outside.json", _LEGACY)
    (source / "linked.json").symlink_to(outside)
    manifest = migrate_tree(source, tmp_path / "new")
    assert manifest.failed == 1
    assert manifest.entries[0].source == "linked.json"
    assert "symbolic link" in manifest.entries[0].reason
    assert manifest.entries[0].source_sha256 == ""


def test_all_output_writes_use_the_atomic_writer(tmp_path, monkeypatch):
    source = tmp_path / "old"
    _write(source, "a.json", _LEGACY)
    calls: list[Path] = []

    from clousight_bench.core import migrate as migrate_mod

    real_write = migrate_mod.atomic_write_text

    def capture(path: Path, text: str) -> Path:
        calls.append(path)
        return real_write(path, text)

    monkeypatch.setattr(migrate_mod, "atomic_write_text", capture)
    dest = tmp_path / "new"
    migrate_tree(source, dest)
    assert calls == [dest / "a.json", dest / MANIFEST_FILE]


def test_dry_run_writes_nothing(tmp_path):
    source = tmp_path / "old"
    dest = tmp_path / "new"
    _write(source, "a/T1.3-run-1.json", _LEGACY)
    manifest = migrate_tree(source, dest, dry_run=True)
    assert manifest.migrated == 1
    assert not dest.exists()


def test_cli_migrate_results_exit_and_output_semantics(tmp_path, capsys):
    source = tmp_path / "old"
    dest = tmp_path / "new"
    _write(source, "a/T1.3-run-1.json", _LEGACY)
    rc = main(["migrate-results", str(source), "--output", str(dest)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "migrated=1" in captured.out
    assert str(dest / MANIFEST_FILE) in captured.out

    broken_source = tmp_path / "broken-old"
    broken_source.mkdir()
    (broken_source / "broken.json").write_text("{not json", encoding="utf-8")
    rc = main(
        [
            "migrate-results",
            str(broken_source),
            "--output",
            str(tmp_path / "broken-new"),
        ]
    )
    assert rc == 1


def test_cli_migrate_results_user_input_errors_exit_two(tmp_path, capsys):
    source = tmp_path / "r"
    source.mkdir()
    rc = main(["migrate-results", str(source), "--output", str(source)])
    assert rc == 2
    assert "in place" in capsys.readouterr().err
