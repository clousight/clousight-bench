"""Deterministic, non-destructive migration from ResultRecord 1.0 to 0.2."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clousight_bench.core.errors import UserInputError
from clousight_bench.core.fingerprints import UNKNOWN, record_digest
from clousight_bench.core.persistence import atomic_write_text
from clousight_bench.core.record import SCHEMA_VERSION, ResultRecord
from clousight_bench.core.redaction import redact, scrub_identities

LEGACY_SCHEMA_VERSION = "1.0"
MANIFEST_FILE = "migration-manifest.json"
_MEASUREMENT_NOTE = "migrated from schema 1.0 metrics"


class MigrationError(UserInputError):
    """The migration request itself cannot be honoured."""


@dataclass
class MigrationEntry:
    source: str
    source_sha256: str
    output: str | None
    status: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_sha256": self.source_sha256,
            "output": self.output,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class MigrationManifest:
    entries: list[MigrationEntry] = field(default_factory=list)

    def _count(self, status: str) -> int:
        return sum(entry.status == status for entry in self.entries)

    @property
    def migrated(self) -> int:
        return self._count("migrated")

    @property
    def skipped(self) -> int:
        return self._count("skipped")

    @property
    def failed(self) -> int:
        return self._count("failed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "migrated": self.migrated,
            "skipped": self.skipped,
            "failed": self.failed,
            "entries": [entry.to_dict() for entry in self.entries],
        }


def _status_and_errors(legacy: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    if legacy.get("ok", True):
        return "completed", []
    message = str(legacy.get("error") or "legacy run reported ok=false without an error message")
    metrics = legacy.get("metrics") or {}
    if isinstance(metrics, dict) and metrics.get("preflight_ok") is False:
        return "invalid", [
            {
                "stage": "PREFLIGHT",
                "code": "legacy_preflight_failed",
                "type": "LegacyError",
                "message": message,
                "retryable": False,
            }
        ]
    return "failed", [
        {
            "stage": "EXECUTE",
            "code": "legacy_error",
            "type": "LegacyError",
            "message": message,
            "retryable": False,
        }
    ]


def _migrated_findings(legacy: dict[str, Any], evidence: str) -> list[dict[str, Any]]:
    findings = legacy.get("findings", [])
    if not isinstance(findings, list):
        raise MigrationError("legacy findings must be a list")
    migrated: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise MigrationError("each legacy finding must be an object")
        item = dict(finding)
        item["evidence"] = evidence
        migrated.append(item)
    return migrated


def migrate_record(legacy: dict[str, Any], *, source_path: str, source_sha256: str) -> dict[str, Any]:
    """Convert one parsed schema 1.0 record into a valid schema 0.2 payload."""
    version = str(legacy.get("schema_version", LEGACY_SCHEMA_VERSION))
    if version != LEGACY_SCHEMA_VERSION:
        raise MigrationError(
            f"{source_path}: expected schema_version {LEGACY_SCHEMA_VERSION!r}, got {version!r}"
        )
    metrics = legacy.get("metrics") or {}
    if not isinstance(metrics, dict):
        raise MigrationError("legacy metrics must be an object")

    safe = scrub_identities(redact(legacy))
    evidence = str(safe.get("evidence_layer", "C"))
    status, errors = _status_and_errors(safe)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run": {
            "run_id": str(safe.get("run_id", "")),
            "started_at": str(safe.get("started_at", "")),
            "finished_at": str(safe.get("finished_at", "")),
            "stages": {},
            "stage_timings": {},
        },
        "identity": {
            "domain": str(safe.get("domain", "")),
            "task_id": str(safe.get("task_id", "")),
            "task_revision": UNKNOWN,
            "scorer_revision": UNKNOWN,
            "adapter": str(safe.get("platform", "")),
            "adapter_status": UNKNOWN,
            "core_version": str(safe.get("runner_version", UNKNOWN)),
            "workload": "",
            "workload_version": "",
            "plugin_versions": {},
        },
        "environment": {
            "region": UNKNOWN,
            "mode": "unknown",
            "python_version": UNKNOWN,
            "os_name": UNKNOWN,
            "facts": {},
            "execution": "unknown",
        },
        "fingerprints": {
            "benchmark": UNKNOWN,
            "environment": UNKNOWN,
            "implementation": UNKNOWN,
            "record_digest": "",
        },
        "measurements": {
            name: {
                "value": value,
                "unit": "",
                "evidence": evidence,
                "notes": _MEASUREMENT_NOTE,
            }
            for name, value in sorted(safe.get("metrics", {}).items())
        },
        "findings": _migrated_findings(safe, evidence),
        "observations": {"legacy_raw": safe.get("raw")},
        "series": safe.get("series", {}),
        "artifacts": safe.get("artifacts", []),
        "extensions": {
            "legacy": {
                "config_hash": safe.get("config_hash", ""),
                "evidence_layer": evidence,
                "raw": safe.get("raw"),
                "ok": safe.get("ok", True),
                "notes": safe.get("notes", ""),
                "runner_version": safe.get("runner_version", ""),
                "source_path": source_path,
                "source_sha256": source_sha256,
            }
        },
        "errors": errors,
        "status": status,
    }
    payload["fingerprints"]["record_digest"] = record_digest(payload)
    ResultRecord.from_dict(payload)
    return payload


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _safe_reason(exc: BaseException, source: Path) -> str:
    message = str(scrub_identities(str(exc))).replace(str(source), "<source>")
    return f"{type(exc).__name__}: {message}"


def migrate_tree(source: Path, dest: Path, *, dry_run: bool = False) -> MigrationManifest:
    """Migrate every JSON file under ``source`` into a fresh destination."""
    source = Path(source).resolve()
    requested_dest = Path(dest)
    dest = requested_dest.resolve()
    if not source.is_dir():
        raise MigrationError(f"source is not a directory: {source}")
    if dest == source:
        raise MigrationError(f"refusing to migrate in place: choose an --output outside {source}")
    if source in dest.parents:
        raise MigrationError(f"refusing to write inside the source tree: {dest} is under {source}")
    if requested_dest.exists() or requested_dest.is_symlink():
        raise MigrationError(f"output already exists; refusing to overwrite: {dest}")

    manifest = MigrationManifest()
    pending: list[tuple[MigrationEntry, dict[str, Any]]] = []
    for path in sorted(source.rglob("*.json")):
        relative = path.relative_to(source).as_posix()
        if path.is_symlink():
            manifest.entries.append(
                MigrationEntry(
                    relative,
                    "",
                    None,
                    "failed",
                    "symbolic link source files are not allowed",
                )
            )
            continue
        sha = ""
        try:
            raw_bytes = path.read_bytes()
            sha = hashlib.sha256(raw_bytes).hexdigest()
            legacy = json.loads(raw_bytes)
            if not isinstance(legacy, dict):
                raise MigrationError("record root must be an object")
            if str(legacy.get("schema_version", "")) == SCHEMA_VERSION:
                manifest.entries.append(
                    MigrationEntry(
                        relative,
                        sha,
                        None,
                        "skipped",
                        f"already schema {SCHEMA_VERSION}",
                    )
                )
                continue
            payload = migrate_record(legacy, source_path=relative, source_sha256=sha)
        except Exception as exc:  # noqa: BLE001 - failures are isolated per file
            manifest.entries.append(MigrationEntry(relative, sha, None, "failed", _safe_reason(exc, source)))
            continue
        entry = MigrationEntry(relative, sha, relative, "migrated")
        manifest.entries.append(entry)
        pending.append((entry, payload))

    if dry_run:
        return manifest

    try:
        dest.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise MigrationError(f"cannot create output directory {dest}: {exc}") from exc

    for entry, payload in pending:
        out_path = dest / entry.source
        try:
            atomic_write_text(out_path, _json_text(payload))
        except Exception as exc:  # noqa: BLE001 - failures are isolated per file
            entry.status = "failed"
            entry.output = None
            entry.reason = _safe_reason(exc, dest)

    try:
        atomic_write_text(dest / MANIFEST_FILE, _json_text(manifest.to_dict()))
    except OSError as exc:
        raise MigrationError(f"cannot write migration manifest: {exc}") from exc
    return manifest
