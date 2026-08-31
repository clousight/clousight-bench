"""Post-run finalize stages: ENRICH (third-party enrichers) and PUBLISH.

Extracted from the orchestrator so that module stays focused on the stage
machine. These run after PERSIST inside ``orchestrator._finish``; an enricher or
publisher bug is recorded, never fatal, and PUBLISH is fully out-of-record (it
writes only append-only receipts). Depends on ``core.stage_support`` for the
shared StageError/log helpers — no import back into the orchestrator.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from clousight_bench.core.canonical import canonical_json
from clousight_bench.core.observation import REPRODUCIBILITY_CLASSES
from clousight_bench.core.publish import (
    ResultPublisher,
    append_receipt,
    begin_publish_attempt,
    capture_trusted_snapshot,
    make_idempotency_key,
    publisher_name,
    receipt_is_json_safe,
    restore_trusted_snapshot,
    safe_error_message,
    snapshot_is_unchanged,
)
from clousight_bench.core.record import ResultRecord, StageError
from clousight_bench.core.registry import load_enrichers
from clousight_bench.core.schema import utc_now
from clousight_bench.core.stage_support import log_traceback as _log_traceback
from clousight_bench.core.stage_support import scrubbed as _scrubbed
from clousight_bench.core.stage_support import stage_error as _stage_error

# Logger name intentionally pinned to the orchestrator (not __name__) so stage
# log records keep a single, stable source after this extraction — do not "fix".
logger = logging.getLogger("clousight_bench.core.orchestrator")


def _enrich(record: ResultRecord, results_dir: Path, debug: bool) -> ResultRecord:
    """Apply third-party enrichers. Their bugs are recorded, never fatal."""
    run_id = record.run.run_id
    failed = False
    try:
        enrichers = load_enrichers()
    except Exception as exc:  # noqa: BLE001 - a broken plugin must not eat the result
        record.errors.append(_stage_error("ENRICH", exc, code="enricher_load_failed").to_dict())
        _log_traceback(results_dir, run_id, debug, exc)
        enrichers = []
        failed = True

    for enricher in enrichers:
        name = getattr(enricher, "name", type(enricher).__name__)
        baseline = record
        try:
            enriched = enricher.enrich(deepcopy(baseline))
        except Exception as exc:  # noqa: BLE001
            record.errors.append(
                _scrubbed(
                    StageError(
                        stage="ENRICH",
                        code="enricher_failed",
                        type=type(exc).__name__,
                        message=f"{name}: {exc}",
                        retryable=False,
                    )
                ).to_dict()
            )
            _log_traceback(results_dir, run_id, debug, exc)
            failed = True
            continue
        if not isinstance(enriched, ResultRecord):
            exc = TypeError(f"enricher {name!r} returned {type(enriched).__name__}, not a ResultRecord")
            record.errors.append(
                _scrubbed(
                    StageError(
                        stage="ENRICH",
                        code="enricher_failed",
                        type=type(exc).__name__,
                        message=str(exc),
                        retryable=False,
                    )
                ).to_dict()
            )
            failed = True
            continue
        try:
            _validate_enriched_record(enriched, baseline)
        except Exception as exc:  # noqa: BLE001 - discard every malformed candidate
            record.errors.append(
                _stage_error(
                    "ENRICH",
                    exc,
                    code=f"enricher_invalid_record:{name}",
                ).to_dict()
            )
            failed = True
            continue
        record = enriched

    record.run.stages["ENRICH"] = "failed" if failed else "ok"
    return record


def _publish(
    record_path: Path,
    results_dir: Path,
    publisher: ResultPublisher | None,
    debug: bool,
) -> None:
    """Publish only a reloaded, verified durable record; receipts own outcome."""
    if publisher is None:
        return

    name, name_error = publisher_name(publisher)
    try:
        snapshot = capture_trusted_snapshot(record_path, results_dir)
        trusted = snapshot.record
    except Exception as exc:  # noqa: BLE001 - fail closed before remote side effects
        _append_publish_receipt(
            results_dir,
            {
                "publisher": name,
                "at": utc_now(),
                "state": "failed",
                "ok": False,
                "publisher_called": False,
                "code": "persisted_record_invalid",
                "type": type(exc).__name__,
                "message": safe_error_message(exc),
            },
            "unknown",
            debug,
        )
        _log_traceback(results_dir, "unknown", debug, exc)
        return

    key = make_idempotency_key(trusted, name)
    base: dict[str, Any] = {
        "run_id": trusted.run.run_id,
        "publisher": name,
        "idempotency_key": key,
        "record_digest": trusted.fingerprints.record_digest,
        "at": utc_now(),
    }
    if name_error is not None:
        _append_publish_receipt(
            results_dir,
            {
                **base,
                "state": "failed",
                "ok": False,
                "publisher_called": False,
                "code": "publisher_name_invalid",
                "type": type(name_error).__name__,
                "message": safe_error_message(name_error),
            },
            trusted.run.run_id,
            debug,
        )
        _log_traceback(results_dir, trusted.run.run_id, debug, name_error)
        return

    try:
        reservation = begin_publish_attempt(
            results_dir,
            run_id=trusted.run.run_id,
            publisher=name,
            idempotency_key=key,
            record_digest=trusted.fingerprints.record_digest,
            at=base["at"],
        )
    except Exception as exc:  # noqa: BLE001 - no pending receipt means no remote call
        _log_traceback(results_dir, trusted.run.run_id, debug, exc)
        return
    if not reservation.should_publish:
        return

    detail: Any = None
    publish_error: BaseException | None = None
    try:
        detail = publisher.publish(deepcopy(trusted))
    except Exception as exc:  # noqa: BLE001 - remote failure belongs in the receipt
        publish_error = exc

    terminal = {
        **base,
        "attempt_id": reservation.attempt_id,
        "publisher_called": True,
    }
    tampering_error: BaseException | None = None
    try:
        if not snapshot_is_unchanged(snapshot, results_dir):
            raise ValueError("persisted result bytes changed after publisher invocation")
    except Exception as exc:  # noqa: BLE001 - extension may have tampered out-of-process
        tampering_error = exc

    if tampering_error is not None:
        try:
            restore_trusted_snapshot(snapshot, results_dir)
            code = "publisher_tampering_restored"
            message = safe_error_message(tampering_error)
        except Exception as restore_error:  # noqa: BLE001 - report loss of containment
            code = "publisher_tampering_restore_failed"
            message = safe_error_message(restore_error)
        terminal.update(
            {
                "state": "indeterminate",
                "ok": False,
                "code": code,
                "type": type(tampering_error).__name__,
                "message": message,
            }
        )
    else:
        if publish_error is not None:
            terminal.update(
                {
                    "state": "indeterminate",
                    "ok": False,
                    "code": "publisher_called_outcome_indeterminate",
                    "type": type(publish_error).__name__,
                    "message": safe_error_message(publish_error),
                }
            )
        elif not isinstance(detail, dict) or not receipt_is_json_safe({"detail": detail}):
            terminal.update(
                {
                    "state": "indeterminate",
                    "ok": False,
                    "code": "publish_detail_invalid",
                    "type": type(detail).__name__,
                    "message": "publisher returned a non-JSON detail dict",
                }
            )
        else:
            terminal.update({"state": "success", "ok": True, "detail": detail})

    _append_publish_receipt(results_dir, terminal, trusted.run.run_id, debug)


def _append_publish_receipt(
    results_dir: Path,
    receipt: dict[str, Any],
    run_id: str,
    debug: bool,
) -> bool:
    """Receipt storage failure is observable locally but never escapes."""
    try:
        append_receipt(results_dir, receipt)
    except Exception as exc:  # noqa: BLE001 - preserve the durable core result
        _log_traceback(results_dir, run_id, debug, exc)
        return False
    return True


def _validate_enriched_record(candidate: Any, baseline: ResultRecord) -> None:
    """Accept canonical records while protecting lifecycle-owned fields."""
    if not isinstance(candidate, ResultRecord):
        raise TypeError(f"enricher returned {type(candidate).__name__}, not a ResultRecord")
    payload = candidate.to_dict()
    canonical_json(payload)
    ResultRecord.from_dict(payload)
    for name, measurement in payload["measurements"].items():
        if not isinstance(name, str) or not isinstance(measurement, dict):
            raise TypeError("measurement names and values must be strings and objects")
        missing = {"value", "unit"} - measurement.keys()
        if missing:
            raise ValueError(f"measurement {name!r} missing keys {sorted(missing)}")
        rclass = measurement.get("reproducibility_class")
        if rclass is not None and rclass not in REPRODUCIBILITY_CLASSES:
            raise ValueError(f"measurement {name!r} has invalid reproducibility_class")
    for finding in payload["findings"]:
        if not isinstance(finding, dict):
            raise TypeError("findings must contain objects")
        missing = {"code", "severity", "summary", "details"} - finding.keys()
        if missing:
            raise ValueError(f"finding missing keys {sorted(missing)}")
    for error in payload["errors"]:
        if not isinstance(error, dict):
            raise TypeError("errors must contain objects")
        fields = {"stage", "code", "type", "message", "retryable"}
        missing = fields - error.keys()
        if missing:
            raise ValueError(f"stage error missing keys {sorted(missing)}")
        extra = error.keys() - fields
        if extra:
            raise ValueError(f"stage error has unknown keys {sorted(extra)}")
        StageError(**error)
    before = baseline.to_dict()
    protected = (
        "schema_version",
        "run",
        "identity",
        "environment",
        "fingerprints",
        "status",
        "measurements",
        "findings",
        "observations",
        "series",
        "artifacts",
        "errors",
    )
    changed = [key for key in protected if payload[key] != before[key]]
    if changed:
        raise ValueError(f"enricher changed lifecycle-owned field(s): {changed}")
