"""The publishing boundary: a place to send a result, and proof of the attempt.

Phase 1B ships the interface and nothing that implements it. A publisher is
injected explicitly -- it is deliberately not discovered through an entry point,
because entry-point discovery needs the API-range and conflict governance that
belongs to Phase 1D.

PUBLISH runs after PERSIST and can never rewrite the core record. Every state
transition appends one line to an append-only receipt file: a durable pending
event is written before the remote call and a terminal event follows when
possible. A failed or indeterminate upload is evidence rather than a silent gap.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clousight_bench.core.fingerprints import record_digest
from clousight_bench.core.persistence import atomic_write_bytes
from clousight_bench.core.record import ResultRecord
from clousight_bench.core.redaction import (
    find_identity_leaks,
    identity_values,
    redact,
    scrub_identities,
    scrub_identity_text,
)
from clousight_bench.core.store import validate_sidecar

RECEIPTS_FILE = "publish-receipts.jsonl"
_RECEIPT_LOCK = threading.RLock()
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class ResultPublisher(ABC):
    """Send a persisted record somewhere without mutating it.

    Implementations MUST use ``record.fingerprints.record_digest`` as their
    remote idempotency token. Core reserves a stable receipt idempotency key
    derived from that digest plus the normalized publisher name before calling
    this method, but keeps this one-argument signature for plugin compatibility.
    """

    name: str = "abstract"

    @abstractmethod
    def publish(self, record: ResultRecord) -> dict[str, Any]:
        """Idempotently publish and return a non-secret JSON detail dict."""


@dataclass(frozen=True)
class PublishReservation:
    should_publish: bool
    idempotency_key: str
    attempt_id: str


@dataclass(frozen=True)
class TrustedRecordSnapshot:
    """Exact durable bytes around one synchronous publisher invocation."""

    record: ResultRecord
    record_path: Path
    record_bytes: bytes
    sidecar_path: Path | None
    sidecar_bytes: bytes | None


def publisher_name(publisher: ResultPublisher) -> tuple[str, BaseException | None]:
    """Read and normalize an extension-owned name without trusting the property."""
    fallback = "publisher"
    try:
        fallback = _normalize_name(type(publisher).__name__) or fallback
    except Exception:  # noqa: BLE001 - a hostile metaclass is extension code too
        pass
    try:
        raw = publisher.name
        if not isinstance(raw, str):
            raise TypeError(f"publisher name must be a string, got {type(raw).__name__}")
        normalized = _normalize_name(raw)
        if not normalized:
            raise ValueError("publisher name is empty after normalization")
        return normalized, None
    except Exception as exc:  # noqa: BLE001 - even name is extension code
        return fallback or "publisher", exc


def load_trusted_record(path: Path, results_dir: Path) -> ResultRecord:
    """Reload and verify the exact durable record a publisher may consume."""
    return capture_trusted_snapshot(path, results_dir).record


def capture_trusted_snapshot(path: Path, results_dir: Path) -> TrustedRecordSnapshot:
    """Validate and retain the exact record and referenced sidecar bytes."""
    record_path = Path(path)
    record_bytes = record_path.read_bytes()
    payload = json.loads(record_bytes)
    if not isinstance(payload, dict):
        raise TypeError("persisted result must be a JSON object")
    record = ResultRecord.from_dict(payload)
    expected = payload.get("fingerprints", {}).get("record_digest")
    if not isinstance(expected, str) or record_digest(payload) != expected:
        raise ValueError("persisted result record_digest mismatch")
    leaks = find_identity_leaks(payload)
    if leaks:
        raise ValueError(f"persisted result contains operator identity at {leaks}")
    sidecar_path, sidecar_error = validate_sidecar(Path(results_dir), payload)
    if sidecar_error is not None:
        raise ValueError(sidecar_error)
    if record.run.stages.get("PERSIST") != "ok":
        raise ValueError("persisted result is not trusted: PERSIST is not ok")
    sidecar_bytes = sidecar_path.read_bytes() if sidecar_path is not None else None
    if sidecar_path is not None:
        confirmed_path, confirmed_error = validate_sidecar(Path(results_dir), payload)
        if confirmed_error is not None or confirmed_path != sidecar_path:
            raise ValueError(confirmed_error or "sidecar path changed while snapshotting")
        if sidecar_path.read_bytes() != sidecar_bytes:
            raise ValueError("sidecar bytes changed while snapshotting")
    return TrustedRecordSnapshot(
        record=record,
        record_path=record_path,
        record_bytes=record_bytes,
        sidecar_path=sidecar_path,
        sidecar_bytes=sidecar_bytes,
    )


def snapshot_is_unchanged(snapshot: TrustedRecordSnapshot, results_dir: Path) -> bool:
    """Revalidate content and require byte-for-byte equality."""
    current = capture_trusted_snapshot(snapshot.record_path, results_dir)
    return (
        current.record_bytes == snapshot.record_bytes
        and current.sidecar_path == snapshot.sidecar_path
        and current.sidecar_bytes == snapshot.sidecar_bytes
    )


def restore_trusted_snapshot(snapshot: TrustedRecordSnapshot, results_dir: Path) -> None:
    """Atomically restore synchronous tampering, then verify the restoration.

    A publisher can still launch a background process that tampers again after
    this check. Preventing sustained out-of-process mutation requires the
    Phase 1D sandbox boundary.
    """
    if snapshot.sidecar_path is not None and snapshot.sidecar_bytes is not None:
        atomic_write_bytes(snapshot.sidecar_path, snapshot.sidecar_bytes)
    atomic_write_bytes(snapshot.record_path, snapshot.record_bytes)
    if not snapshot_is_unchanged(snapshot, results_dir):
        raise RuntimeError("restored persisted result did not remain stable")


def make_idempotency_key(record: ResultRecord, name: str) -> str:
    """Stable key for one publisher and one immutable result."""
    material = f"{record.fingerprints.record_digest}\0{name}".encode()
    return "sha256:" + hashlib.sha256(material).hexdigest()


def begin_publish_attempt(
    results_dir: Path,
    *,
    run_id: str,
    publisher: str,
    idempotency_key: str,
    record_digest: str,
    at: str,
) -> PublishReservation:
    """Atomically deduplicate and durably reserve one remote publish attempt."""
    attempt_id = uuid.uuid4().hex
    pending = {
        "run_id": run_id,
        "publisher": publisher,
        "idempotency_key": idempotency_key,
        "record_digest": record_digest,
        "attempt_id": attempt_id,
        "at": at,
        "state": "pending",
        "ok": False,
        "publisher_called": False,
    }
    with _locked_receipts(results_dir, readable=True) as (fd, _):
        receipts = _read_receipts(fd)
        same_key = [item for item in receipts if item.get("idempotency_key") == idempotency_key]
        if any(item.get("state") == "success" for item in same_key):
            _write_receipt(
                fd,
                {
                    **pending,
                    "state": "skipped",
                    "code": "already_published",
                },
            )
            return PublishReservation(False, idempotency_key, attempt_id)

        if any(item.get("state") == "indeterminate" for item in same_key):
            _write_receipt(
                fd,
                {
                    **pending,
                    "state": "indeterminate",
                    "code": "prior_attempt_indeterminate",
                },
            )
            return PublishReservation(False, idempotency_key, attempt_id)

        if any(
            item.get("state") == "failed" and item.get("publisher_called") is not False for item in same_key
        ):
            _write_receipt(
                fd,
                {
                    **pending,
                    "state": "indeterminate",
                    "code": "prior_called_failure",
                },
            )
            return PublishReservation(False, idempotency_key, attempt_id)

        terminals = {
            item.get("attempt_id")
            for item in same_key
            if item.get("state") in {"success", "failed", "indeterminate"}
        }
        unresolved = [
            item
            for item in same_key
            if item.get("state") == "pending" and item.get("attempt_id") not in terminals
        ]
        if unresolved:
            _write_receipt(
                fd,
                {
                    **pending,
                    "state": "indeterminate",
                    "code": "prior_attempt_pending",
                },
            )
            return PublishReservation(False, idempotency_key, attempt_id)

        _write_receipt(fd, pending)
    return PublishReservation(True, idempotency_key, attempt_id)


def append_receipt(results_dir: Path, receipt: dict[str, Any]) -> Path:
    """Durably append one private, compact and scrubbed publish event."""
    safe_receipt = _safe_receipt(receipt)
    with _locked_receipts(results_dir) as (fd, path):
        _write_receipt(fd, safe_receipt, already_safe=True)
    return path


def receipt_is_json_safe(receipt: dict[str, Any]) -> bool:
    """Whether a receipt can be safely encoded after redaction."""
    try:
        _safe_receipt(receipt)
    except Exception:  # noqa: BLE001 - detail is extension-owned container code
        return False
    return True


def safe_error_message(exc: BaseException) -> str:
    """Scrub an extension or validation failure before receipt persistence."""
    try:
        return scrub_identity_text(str(exc))
    except Exception:  # noqa: BLE001 - hostile exceptions can break __str__
        return "<unavailable>"


def _normalize_name(value: str) -> str:
    return _SAFE_NAME.sub("-", value.strip())[:80].strip("-._")


def _safe_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    safe = redact(receipt)
    # This is a public sha256 deduplication token, not credential material.
    # The generic redactor treats every "*key" field as secret, so preserve
    # this one structural field explicitly.
    if isinstance(receipt.get("idempotency_key"), str):
        safe["idempotency_key"] = receipt["idempotency_key"]
    safe = scrub_identities(safe, identities=identity_values())
    json.dumps(
        safe,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return safe


@contextmanager
def _locked_receipts(results_dir: Path, *, readable: bool = False) -> Iterator[tuple[int, Path]]:
    root = Path(results_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / RECEIPTS_FILE
    flags = os.O_CREAT | os.O_APPEND | (os.O_RDWR if readable else os.O_WRONLY)
    with _RECEIPT_LOCK:
        try:
            fd = os.open(path, flags | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            fd = os.open(path, flags, 0o600)
            created = False
        try:
            os.fchmod(fd, 0o600)
            if created:
                _fsync_directory(root)
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX)
            except ImportError:  # pragma: no cover - Windows has the thread lock
                pass
            yield fd, path
        finally:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            except ImportError:  # pragma: no cover
                pass
            os.close(fd)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_receipts(fd: int) -> list[dict[str, Any]]:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while chunk := os.read(fd, 65536):
        chunks.append(chunk)
    text = b"".join(chunks).decode("utf-8")
    receipts: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid receipt JSON at line {number}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"receipt line {number} is not an object")
        receipts.append(item)
    return receipts


def _write_receipt(fd: int, receipt: dict[str, Any], *, already_safe: bool = False) -> None:
    safe = receipt if already_safe else _safe_receipt(receipt)
    data = (
        json.dumps(
            safe,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]
    os.fsync(fd)
