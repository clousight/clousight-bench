"""ResultStore: persist a ResultRecord 0.2, or say loudly where it went instead.

Record layout stays ``results/<domain>/<adapter>/<task_id>-<run_id>.json`` so
existing tooling keeps finding results. Three promises hold on every path:

1. **Nothing is lost.** Any failure while rendering or writing degrades to a
   smaller record rather than to no record: raw evidence is dropped first, the
   scored fields next, and only then does the write move to the system temp
   directory. The record always says, in its own ``errors``, what happened.
2. **Nothing is invented.** ``run.stages["PERSIST"]`` becomes ``ok`` on the
   record only once the bytes are on disk; a failed write leaves ``failed``
   plus a ``PERSIST`` stage error in both the file and the returned object.
3. **What we return is what we wrote.** The caller's record is mutated to match
   the payload byte-for-byte in meaning (series pointer, scrubbed messages,
   dropped evidence, record digest), so a printed record can be verified
   against the file.

With the optional [store] extra (duckdb + pyarrow) a record's series is
externalized to a per-run Parquet long table and the record's ``series`` field
becomes a pointer carrying the sidecar's own sha256, so the record digest
covers the sidecar too. Long-table columns (the stable handshake for
cb-dataservice and the SaaS web):

    run_id | domain | task_id | platform | benchmark_fingerprint | series | t | value | unit
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import sys
import uuid
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from clousight_bench.core.fingerprints import record_digest
from clousight_bench.core.persistence import (
    atomic_write_bytes,
    atomic_write_text,
    emergency_write_text,
)
from clousight_bench.core.record import ResultRecord, StageError
from clousight_bench.core.redaction import (
    SensitiveDataError,
    find_identity_leaks,
    scrub_identities,
    scrub_identity_text,
)
from clousight_bench.core.schema_validate import (
    SchemaValidationError,
    validate_against_schema,
)

try:  # optional [store] extra
    import duckdb  # noqa: F401
    import pyarrow  # noqa: F401

    STORE_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on install extras
    STORE_AVAILABLE = False

logger = logging.getLogger(__name__)

_LONG_COLUMNS = [
    "run_id", "domain", "task_id", "platform", "benchmark_fingerprint",
    "series", "t", "value", "unit",
]


@dataclass
class _Sidecar:
    """A rendered, not-yet-written Parquet series table and its identity."""

    path: Path
    relpath: str
    data: bytes
    sha256: str
    rows: int

    def pointer(self) -> dict[str, Any]:
        return {"$parquet": self.relpath, "sha256": self.sha256, "rows": self.rows}


def validate_sidecar(
    results_dir: Path, payload: dict[str, Any]
) -> tuple[Path | None, str | None]:
    """Resolve and verify the sidecar referenced by one trusted record payload."""
    pointer = payload.get("series")
    if not isinstance(pointer, dict) or "$parquet" not in pointer:
        return None, None
    relpath = pointer.get("$parquet")
    expected_sha = pointer.get("sha256")
    expected_rows = pointer.get("rows")
    if (
        not isinstance(relpath, str)
        or not isinstance(expected_sha, str)
        or not isinstance(expected_rows, int)
        or isinstance(expected_rows, bool)
        or expected_rows < 0
    ):
        return None, "malformed sidecar pointer"
    root = Path(results_dir).resolve()
    path = (root / relpath).resolve()
    if not path.is_relative_to(root):
        return None, "sidecar path escapes the results directory"
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, f"sidecar unreadable: {exc}"
    actual_sha = "sha256:" + hashlib.sha256(data).hexdigest()
    if actual_sha != expected_sha:
        return None, "sidecar sha256 mismatch"
    try:
        import pyarrow.parquet as pq

        actual_rows = pq.ParquetFile(path).metadata.num_rows
    except (ImportError, OSError, ValueError) as exc:
        return None, f"sidecar unreadable: {exc}"
    if actual_rows != expected_rows:
        return None, "sidecar rows mismatch"
    return path, None


def _drop_evidence(record: ResultRecord) -> None:
    """Drop the parts a plugin filled in freely -- the usual un-encodable culprit."""
    record.observations = {}
    record.series = {}
    record.artifacts = []
    record.environment.facts = {}


def _drop_scored(record: ResultRecord) -> None:
    """Keep only what the core itself produced: identity, stages, status, errors."""
    _drop_evidence(record)
    record.measurements = {}
    record.findings = []
    record.extensions = {"core": {"persistence_degraded": True}}


class ResultStore:
    def __init__(self, results_dir: Path) -> None:
        self.results_dir = Path(results_dir)

    def _record_path(self, rec: ResultRecord) -> Path:
        out_dir = self.results_dir / rec.identity.domain / rec.identity.adapter
        return out_dir / f"{rec.identity.task_id}-{rec.run.run_id}.json"

    def _run_dir(self, rec: ResultRecord) -> Path:
        return self.results_dir / rec.identity.domain / rec.identity.adapter / rec.run.run_id

    # --- the one entry point -------------------------------------------------

    def persist(self, record: ResultRecord) -> Path:
        """Write ``record`` and return the path it actually reached."""
        record.errors = [_scrubbed_error(error) for error in record.errors]
        inline_series = dict(record.series)
        sidecar = self._series_sidecar(record)
        written_sidecar: Path | None = None

        try:
            payload = self._payload(record, sidecar, "ok")
            if sidecar is not None:
                # After validation, never before: a refused record must not
                # leave a sidecar behind, and the pointer is already hashed.
                try:
                    atomic_write_bytes(sidecar.path, sidecar.data)
                    written_sidecar = sidecar.path
                except OSError as exc:
                    logger.warning(
                        "run %s: keeping the series inline, sidecar write failed: %s",
                        record.run.run_id,
                        exc,
                    )
                    _remove_empty_run_dir(self._run_dir(record))
                    sidecar = None
                    payload = self._payload(record, None, "ok")
            path = atomic_write_text(self._record_path(record), _dump(payload))
        except SchemaValidationError:
            # A malformed record must fail loudly, not be silently downgraded to
            # a degraded write. The raw record was already emergency-dumped.
            raise
        except Exception as exc:  # noqa: BLE001 - losing a result is the worst outcome
            return self._persist_degraded(record, inline_series, written_sidecar, exc)

        if sidecar is not None:
            record.series = sidecar.pointer()
        record.fingerprints.record_digest = payload["fingerprints"]["record_digest"]
        record.run.stages["PERSIST"] = "ok"
        return path

    # --- rendering -----------------------------------------------------------

    def _payload(
        self,
        record: ResultRecord,
        sidecar: _Sidecar | None,
        stage_state: str,
        scrub: bool = False,
    ) -> dict[str, Any]:
        """Render the payload to write, or raise if it must not be written."""
        payload = record.to_dict()
        payload["run"]["stages"] = {**payload["run"]["stages"], "PERSIST": stage_state}
        if sidecar is not None:
            payload["series"] = sidecar.pointer()
        if scrub:
            # Last resort: publish a scrubbed record rather than none at all.
            payload = scrub_identities(payload)
        else:
            leaks = find_identity_leaks(payload)
            if leaks:
                raise SensitiveDataError(
                    f"refusing to persist run {record.run.run_id}: "
                    f"operator-identifying values at {leaks}"
                )
        payload["fingerprints"]["record_digest"] = record_digest(payload)
        self._assert_schema(record, payload)
        return payload

    def _assert_schema(self, record: ResultRecord, payload: dict[str, Any]) -> None:
        """Refuse to persist a record that fails the published 0.2 schema.

        Hard fail -- but never lose the produced record: dump it raw first so no
        measurement vanishes because the shape was wrong."""
        try:
            validate_against_schema(payload, "result-record-0.2")
        except SchemaValidationError:
            name = (
                f"INVALID-{record.identity.domain}-{record.identity.task_id}"
                f"-{record.run.run_id}.json"
            )
            try:
                dump_path = _emergency_write_unique(name, _dump(payload))
                print(
                    f"clousight-bench: run {record.run.run_id} produced a record that "
                    f"fails the 0.2 schema; raw record dumped to {dump_path}",
                    file=sys.stderr,
                )
            except Exception:  # noqa: BLE001 - the dump is best-effort
                pass
            raise

    def _persist_degraded(
        self,
        record: ResultRecord,
        inline_series: dict[str, Any],
        written_sidecar: Path | None,
        exc: BaseException,
    ) -> Path:
        """A write we could not complete as asked, completed as well as we can."""
        if written_sidecar is not None:
            _isolate_or_remove_sidecar(written_sidecar)
        record.series = dict(inline_series)  # never point at a sidecar we removed
        record.run.stages["PERSIST"] = "failed"
        record.status = "failed"
        record.extensions = {"core": {"persistence_degraded": True}}
        error = _persist_error(exc)
        record.errors.append(error)

        payload = self._degraded_payload(record)
        text = _dump(payload)
        replacement = ResultRecord.from_dict(payload)
        record.__dict__.update(replacement.__dict__)

        if not isinstance(exc, OSError):
            # The results directory itself is fine; keep the record where the
            # user asked for it, just smaller and honest about being degraded.
            try:
                path = atomic_write_text(self._record_path(record), text)
            except OSError:
                path = None
            if path is not None:
                print(
                    f"clousight-bench: refused to persist the full record for run "
                    f"{record.run.run_id} ({error['code']}: {exc}); "
                    f"a degraded record was written to {path}",
                    file=sys.stderr,
                )
                return path

        name = (
            f"{record.identity.domain}-{record.identity.task_id}"
            f"-{record.run.run_id}.json"
        )
        try:
            path = _emergency_write_unique(name, text)
        except Exception:  # noqa: BLE001 - the record must not vanish in silence
            print(
                "clousight-bench: could not write the results directory or the "
                f"emergency directory ({exc}); the record follows on stderr:\n{text}",
                file=sys.stderr,
            )
            raise
        print(
            f"clousight-bench: could not write the results directory ({exc}); "
            f"emergency record written to {path}",
            file=sys.stderr,
        )
        return path

    def _degraded_payload(self, record: ResultRecord) -> dict[str, Any]:
        """Shrink the record until it can be rendered safely."""
        for drop in (None, _drop_evidence, _drop_scored):
            if drop is not None:
                drop(record)
            try:
                return self._payload(record, None, "failed")
            except Exception:  # noqa: BLE001 - try the next, smaller shape
                continue
        try:
            return self._payload(record, None, "failed", scrub=True)
        except Exception:  # noqa: BLE001 - core-owned minimum must always encode
            return _minimal_payload(record)

    # --- optional Parquet sidecar -------------------------------------------

    def _series_sidecar(self, record: ResultRecord) -> _Sidecar | None:
        if not STORE_AVAILABLE or not record.series or "$parquet" in record.series:
            return None
        try:
            return self._build_series_sidecar(record)
        except Exception as exc:  # noqa: BLE001 - the sidecar is an optimisation
            _remove_empty_run_dir(self._run_dir(record))
            logger.warning(
                "run %s: keeping the series inline, sidecar failed: %s",
                record.run.run_id,
                exc,
            )
            return None

    def _build_series_sidecar(self, record: ResultRecord) -> _Sidecar:
        import pyarrow as pa
        import pyarrow.parquet as pq

        rows: dict[str, list] = {c: [] for c in _LONG_COLUMNS}
        count = 0
        for series_name, points in record.series.items():
            measurement = record.measurements.get(series_name, {})
            unit = str(measurement.get("unit", "")) if isinstance(measurement, dict) else ""
            for t, value in points:
                rows["run_id"].append(record.run.run_id)
                rows["domain"].append(record.identity.domain)
                rows["task_id"].append(record.identity.task_id)
                rows["platform"].append(record.identity.adapter)
                rows["benchmark_fingerprint"].append(record.fingerprints.benchmark)
                rows["series"].append(series_name)
                rows["t"].append(t)
                rows["value"].append(float(value))
                rows["unit"].append(unit)
                count += 1

        leaks = find_identity_leaks(rows)
        if leaks:
            raise SensitiveDataError(
                f"refusing operator-identifying values in series sidecar at {leaks}"
            )
        buffer = io.BytesIO()
        pq.write_table(pa.table(rows), buffer)
        data = buffer.getvalue()
        path = self._run_dir(record) / "series.parquet"
        return _Sidecar(
            path=path,
            relpath=path.relative_to(self.results_dir).as_posix(),
            data=data,
            sha256="sha256:" + hashlib.sha256(data).hexdigest(),
            rows=count,
        )

    def query_series(
        self, sql: str | None = None, glob: str = "**/series.parquet"
    ) -> list[dict[str, Any]]:
        if not STORE_AVAILABLE:
            raise ImportError(
                "query_series needs the [store] extra: pip install clousight-bench[store]"
            )
        import duckdb

        paths: list[str] = []
        for record_path in sorted(self.results_dir.rglob("*.json")):
            try:
                payload = json.loads(record_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    continue
                expected = payload.get("fingerprints", {}).get("record_digest")
                if not isinstance(expected, str) or record_digest(payload) != expected:
                    continue
                sidecar, error = validate_sidecar(self.results_dir, payload)
                if error is not None or sidecar is None:
                    continue
                relative = sidecar.relative_to(self.results_dir.resolve()).as_posix()
                if fnmatch(relative, glob):
                    paths.append(str(sidecar))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
        if not paths:
            return []
        con = duckdb.connect()
        try:
            # Pass the (possibly glob) path via the relation API, not string
            # interpolation, so paths with quotes / special chars cannot break
            # out of the SQL (parameters aren't allowed inside CREATE VIEW
            # read_parquet).
            con.read_parquet(paths).create_view("series")
            cur = con.execute(sql or "SELECT * FROM series")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            con.close()


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _scrubbed_error(error: Any) -> Any:
    """A stage message may quote a path or a host; neither identifies the run."""
    if not isinstance(error, dict) or "message" not in error:
        return error
    return {**error, "message": scrub_identity_text(str(error["message"]))}


def _persist_error(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, SensitiveDataError):
        code = "identity_leak"
    elif isinstance(exc, OSError):
        code = "persist_unwritable"
    else:
        code = "persist_failed"
    return _scrubbed_error(
        StageError(
            stage="PERSIST",
            code=code,
            type=type(exc).__name__,
            message=str(exc),
            retryable=isinstance(exc, OSError),
        ).to_dict()
    )


def _remove_empty_run_dir(run_dir: Path) -> None:
    """A failed optional sidecar must not leave its empty directory behind."""
    try:
        run_dir.rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("could not remove failed sidecar directory %s", run_dir)


def _isolate_or_remove_sidecar(path: Path) -> None:
    """Delete an orphan, or atomically move it outside the query glob."""
    try:
        path.unlink(missing_ok=True)
        return
    except OSError as exc:
        logger.warning("could not remove orphan sidecar %s: %s", path, exc)
    quarantine = path.with_name(f".{path.name}.quarantine-{uuid.uuid4().hex}")
    try:
        path.replace(quarantine)
    except OSError as exc:
        # query_series only reads sidecars referenced by a digest-valid record,
        # so even a file that cannot be renamed remains invisible.
        logger.error("could not quarantine orphan sidecar %s: %s", path, exc)


def _emergency_write_unique(name: str, text: str) -> Path:
    """Never overwrite another emergency result, even for the same run id."""
    stem = Path(name).stem
    suffix = Path(name).suffix
    for attempt in range(1000):
        candidate = name if attempt == 0 else f"{stem}-{attempt}{suffix}"
        try:
            return emergency_write_text(candidate, text)
        except FileExistsError:
            continue
    raise FileExistsError(f"could not allocate a unique emergency name for {name}")


def _minimal_payload(record: ResultRecord) -> dict[str, Any]:
    """Build a hand-owned canonical 0.2 record when every plugin field is bad."""
    def text(value: Any) -> str:
        try:
            rendered = str(value)
        except Exception:  # noqa: BLE001 - this is the no-plugin-data last resort
            return "<unavailable>"
        return scrub_identity_text(rendered)

    payload: dict[str, Any] = {
        "schema_version": "0.2",
        "run": {
            "run_id": text(record.run.run_id),
            "started_at": text(record.run.started_at),
            "finished_at": text(record.run.finished_at),
            "stages": {"PERSIST": "failed"},
        },
        "identity": {
            "domain": text(record.identity.domain),
            "task_id": text(record.identity.task_id),
            "task_revision": text(record.identity.task_revision),
            "scorer_revision": text(record.identity.scorer_revision),
            "adapter": text(record.identity.adapter),
            "adapter_status": text(record.identity.adapter_status),
            "core_version": text(record.identity.core_version),
            "workload": "",
            "workload_version": "",
            "plugin_versions": {},
        },
        "environment": {
            "region": "",
            "mode": "unknown",
            "python_version": "",
            "os_name": "",
            "facts": {},
        },
        "fingerprints": {
            "benchmark": "unknown",
            "environment": "unknown",
            "implementation": "unknown",
            "record_digest": "",
        },
        "status": "failed",
        "measurements": {},
        "findings": [],
        "observations": {},
        "series": {},
        "artifacts": [],
        "extensions": {"core": {"persistence_degraded": True}},
        "errors": [
            {
                "stage": "PERSIST",
                "code": "persist_failed",
                "type": "CanonicalJSONError",
                "message": "full result was not canonically encodable",
                "retryable": False,
            }
        ],
    }
    payload["fingerprints"]["record_digest"] = record_digest(payload)
    return payload
