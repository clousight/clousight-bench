"""A third-party extension must never be able to change the core verdict."""

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest

import clousight_bench.core.finalize as fin
import clousight_bench.core.orchestrator as orch
from clousight_bench.core.fingerprints import record_digest
from clousight_bench.core.plugin import ResultEnricher
from clousight_bench.core.publish import (
    RECEIPTS_FILE,
    ResultPublisher,
    append_receipt,
    begin_publish_attempt,
)
from clousight_bench.core.record import ResultRecord
from clousight_bench.core.schema import RunSpec

_SPEC = RunSpec(
    "agent-runtime",
    "stub.ok",
    "local-sim",
    target={"recovery": {"mode": "auto-retry"}},
)


class _Boom(ResultEnricher):
    name = "boom"

    def enrich(self, record: ResultRecord) -> ResultRecord:
        raise RuntimeError("pricing dataset unreadable")


class _Good(ResultEnricher):
    name = "good"

    def enrich(self, record: ResultRecord) -> ResultRecord:
        record.extensions["good"] = {"applied": True}
        return record


def _persisted(tmp_path, record):
    return json.loads(_record_path(tmp_path, record).read_text(encoding="utf-8"))


def _record_path(tmp_path, record):
    return tmp_path / "agent-runtime" / "local-sim" / f"stub.ok-{record.run.run_id}.json"


def _receipts(tmp_path):
    return [json.loads(line) for line in (tmp_path / RECEIPTS_FILE).read_text(encoding="utf-8").splitlines()]


def test_a_failing_enricher_does_not_change_the_core_status(monkeypatch, tmp_path):
    monkeypatch.setattr(fin, "load_enrichers", lambda: [_Boom()])
    rec = orch.execute(_SPEC, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements  # stub task emits an "ok" measurement; just verify it's non-empty
    assert rec.run.stages["ENRICH"] == "failed"
    enrich_errors = [e for e in rec.errors if e["stage"] == "ENRICH"]
    assert len(enrich_errors) == 1
    assert enrich_errors[0]["code"] == "enricher_failed"
    assert enrich_errors[0]["type"] == "RuntimeError"
    assert "boom" in enrich_errors[0]["message"]


def test_a_failing_enricher_does_not_block_the_others(monkeypatch, tmp_path):
    monkeypatch.setattr(fin, "load_enrichers", lambda: [_Boom(), _Good()])
    rec = orch.execute(_SPEC, results_dir=tmp_path)
    assert rec.extensions["good"] == {"applied": True}
    assert rec.run.stages["ENRICH"] == "failed"


def test_an_enricher_returning_the_wrong_type_is_rejected(monkeypatch, tmp_path):
    class _Wrong(ResultEnricher):
        name = "wrong"

        def enrich(self, record):
            return {"not": "a record"}

    monkeypatch.setattr(fin, "load_enrichers", lambda: [_Wrong()])
    rec = orch.execute(_SPEC, results_dir=tmp_path)
    assert isinstance(rec, ResultRecord)
    assert rec.status == "completed"
    assert [e["code"] for e in rec.errors if e["stage"] == "ENRICH"] == ["enricher_failed"]


def test_publish_is_off_unless_a_publisher_is_injected(monkeypatch, tmp_path):
    monkeypatch.setattr(fin, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path)
    assert rec.run.stages["PUBLISH"] == "skipped"
    assert not (tmp_path / RECEIPTS_FILE).exists()


def test_a_failing_publisher_writes_a_receipt_and_leaves_the_record_alone(monkeypatch, tmp_path):
    calls = []

    class _BadPublisher(ResultPublisher):
        name = "bad"

        def publish(self, record):
            calls.append(record.run.run_id)
            raise ConnectionError("data service unreachable")

    monkeypatch.setattr(fin, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_BadPublisher())

    assert rec.status == "completed"
    assert rec.run.stages["PUBLISH"] == "skipped"
    receipts = _receipts(tmp_path)
    assert receipts[-2]["state"] == "pending"
    assert receipts[-2]["publisher_called"] is False
    assert receipts[-2]["idempotency_key"] == receipts[-1]["idempotency_key"]
    assert receipts[-1]["state"] == "indeterminate"
    assert receipts[-1]["publisher_called"] is True
    assert receipts[-1]["ok"] is False
    assert receipts[-1]["publisher"] == "bad"
    assert receipts[-1]["run_id"] == rec.run.run_id
    assert receipts[-1]["type"] == "ConnectionError"
    assert receipts[-1]["code"] == "publisher_called_outcome_indeterminate"

    persisted = _persisted(tmp_path, rec)
    assert persisted["status"] == "completed"
    assert [e["stage"] for e in persisted["errors"]] == []

    orch._publish(_record_path(tmp_path, rec), tmp_path, _BadPublisher(), debug=False)
    assert len(calls) == 1
    assert _receipts(tmp_path)[-1]["code"] == "prior_attempt_indeterminate"


def test_a_successful_publisher_writes_an_ok_receipt(monkeypatch, tmp_path):
    class _GoodPublisher(ResultPublisher):
        name = "good"

        def publish(self, record):
            return {"remote_id": "abc"}

    monkeypatch.setattr(fin, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_GoodPublisher())
    assert rec.run.stages["PUBLISH"] == "skipped"
    assert rec.to_dict() == _persisted(tmp_path, rec)
    assert record_digest(rec.to_dict()) == rec.fingerprints.record_digest
    pending, receipt = _receipts(tmp_path)
    assert pending["state"] == "pending"
    assert pending["publisher_called"] is False
    assert pending["record_digest"] == rec.fingerprints.record_digest
    assert receipt["state"] == "success"
    assert receipt["publisher_called"] is True
    assert receipt["ok"] is True
    assert receipt["detail"] == {"remote_id": "abc"}


def test_publisher_receives_the_durable_record_and_cannot_mutate_core(monkeypatch, tmp_path):
    seen = {}

    class _MutatingPublisher(ResultPublisher):
        name = "mutating"

        def publish(self, record):
            seen["record"] = record.to_dict()
            record.status = "failed"
            record.measurements.clear()
            return {"remote_id": "abc"}

    monkeypatch.setattr(fin, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_MutatingPublisher())
    persisted = _persisted(tmp_path, rec)

    assert seen["record"] == persisted
    assert seen["record"]["run"]["stages"]["PERSIST"] == "ok"
    assert rec.status == "completed"
    assert rec.measurements  # stub task emits an "ok" measurement; just verify it's non-empty
    assert persisted["status"] == "completed"
    assert rec.to_dict() == persisted
    assert rec.run.stages["PUBLISH"] == "skipped"
    assert record_digest(persisted) == persisted["fingerprints"]["record_digest"]


def test_receipts_redact_secrets_and_machine_identity(monkeypatch, tmp_path):
    class _LeakyPublisher(ResultPublisher):
        name = "leaky"

        def publish(self, record):
            return {
                "api_token": "super-secret",
                "remote_path": "/home/build-user/results",
            }

    monkeypatch.setattr(fin, "load_enrichers", list)
    monkeypatch.setattr("clousight_bench.core.publish.identity_values", lambda: ("build-user",))
    orch.execute(_SPEC, results_dir=tmp_path, publisher=_LeakyPublisher())
    text = (tmp_path / RECEIPTS_FILE).read_text(encoding="utf-8")

    assert "super-secret" not in text
    assert "build-user" not in text
    assert "<redacted>" in text


def test_publisher_reads_and_validates_the_actual_persisted_path(monkeypatch, tmp_path):
    calls = []

    class _Publisher(ResultPublisher):
        name = "must-not-run"

        def publish(self, record):
            calls.append(record)
            return {}

    original = orch.ResultStore.persist

    def persist_then_tamper(store, record):
        path = original(store, record)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = "failed"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    monkeypatch.setattr(orch.ResultStore, "persist", persist_then_tamper)
    monkeypatch.setattr(fin, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_Publisher())

    assert calls == []
    receipt = _receipts(tmp_path)[-1]
    assert receipt["state"] == "failed"
    assert receipt["publisher_called"] is False
    assert receipt["code"] == "persisted_record_invalid"
    assert rec.run.stages["PUBLISH"] == "skipped"


def test_sidecar_validation_failure_never_calls_the_publisher(monkeypatch, tmp_path):
    calls = []

    class _Publisher(ResultPublisher):
        name = "must-not-run"

        def publish(self, record):
            calls.append(record)
            return {}

    monkeypatch.setattr(fin, "load_enrichers", list)
    monkeypatch.setattr(
        "clousight_bench.core.publish.validate_sidecar",
        lambda results_dir, payload: (None, "sidecar sha256 mismatch"),
    )
    orch.execute(_SPEC, results_dir=tmp_path, publisher=_Publisher())

    assert calls == []
    assert _receipts(tmp_path)[-1]["code"] == "persisted_record_invalid"


def test_sidecar_changing_during_snapshot_fails_closed(monkeypatch, tmp_path):
    calls = []
    checks = []

    class _Publisher(ResultPublisher):
        name = "must-not-run"

        def publish(self, record):
            calls.append(record)
            return {}

    monkeypatch.setattr(fin, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path)
    path = _record_path(tmp_path, rec)
    payload = _persisted(tmp_path, rec)
    sidecar = path.parent / "evidence.bin"
    sidecar.write_bytes(b"trusted")
    payload["series"] = {
        "$parquet": sidecar.relative_to(tmp_path).as_posix(),
        "sha256": "test-only",
        "rows": 1,
    }
    payload["fingerprints"]["record_digest"] = record_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    def changing_validation(results_dir, data):
        checks.append(True)
        if len(checks) == 1:
            return sidecar, None
        return None, "sidecar changed while snapshotting"

    monkeypatch.setattr(
        "clousight_bench.core.publish.validate_sidecar",
        changing_validation,
    )
    orch._publish(path, tmp_path, _Publisher(), debug=False)

    assert calls == []
    assert len(checks) == 2
    assert _receipts(tmp_path)[-1]["code"] == "persisted_record_invalid"


@pytest.mark.parametrize("tamper", ["schema", "identity"])
def test_schema_and_identity_validation_fail_closed(monkeypatch, tmp_path, tamper):
    calls = []

    class _Publisher(ResultPublisher):
        name = "must-not-run"

        def publish(self, record):
            calls.append(record)
            return {}

    original = orch.ResultStore.persist

    def persist_then_tamper(store, record):
        path = original(store, record)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if tamper == "schema":
            payload["schema_version"] = "1.0"
        else:
            payload["environment"]["facts"]["host"] = "build-user"
            monkeypatch.setattr(
                "clousight_bench.core.publish.identity_values",
                lambda: ("build-user",),
            )
            monkeypatch.setattr(
                "clousight_bench.core.redaction.identity_values",
                lambda: ("build-user",),
            )
        payload["fingerprints"]["record_digest"] = record_digest(payload)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    monkeypatch.setattr(orch.ResultStore, "persist", persist_then_tamper)
    monkeypatch.setattr(fin, "load_enrichers", list)
    orch.execute(_SPEC, results_dir=tmp_path, publisher=_Publisher())

    assert calls == []
    assert _receipts(tmp_path)[-1]["code"] == "persisted_record_invalid"


def test_pending_receipt_failure_prevents_the_remote_call(monkeypatch, tmp_path):
    calls = []

    class _Publisher(ResultPublisher):
        name = "remote"

        def publish(self, record):
            calls.append(record)
            return {}

    monkeypatch.setattr(fin, "load_enrichers", list)
    monkeypatch.setattr(
        fin,
        "begin_publish_attempt",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("receipt read-only")),
    )
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_Publisher())

    assert calls == []
    assert rec.run.stages["PUBLISH"] == "skipped"


def test_name_and_failure_receipt_errors_are_both_isolated(monkeypatch, tmp_path):
    calls = []

    class _Publisher(ResultPublisher):
        @property
        def name(self):
            raise RuntimeError("name exploded")

        def publish(self, record):
            calls.append(record)
            return {}

    monkeypatch.setattr(fin, "load_enrichers", list)
    monkeypatch.setattr(
        fin,
        "append_receipt",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read-only")),
    )
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_Publisher())

    assert calls == []
    assert rec.run.stages["PUBLISH"] == "skipped"
    assert record_digest(rec.to_dict()) == rec.fingerprints.record_digest


def test_terminal_receipt_failure_keeps_pending_and_retry_does_not_republish(monkeypatch, tmp_path):
    calls = []

    class _Publisher(ResultPublisher):
        name = "remote"

        def publish(self, record):
            calls.append(record.fingerprints.record_digest)
            return {"remote_id": "sent"}

    original_append = fin.append_receipt

    def fail_terminal(results_dir, receipt):
        if receipt.get("state") == "success":
            raise OSError("disk full")
        return original_append(results_dir, receipt)

    monkeypatch.setattr(fin, "load_enrichers", list)
    monkeypatch.setattr(fin, "append_receipt", fail_terminal)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_Publisher())
    path = _record_path(tmp_path, rec)

    assert len(calls) == 1
    assert [item["state"] for item in _receipts(tmp_path)] == ["pending"]

    monkeypatch.setattr(fin, "append_receipt", original_append)
    orch._publish(path, tmp_path, _Publisher(), debug=False)
    assert len(calls) == 1
    assert _receipts(tmp_path)[-1]["state"] == "indeterminate"
    assert _receipts(tmp_path)[-1]["code"] == "prior_attempt_pending"


def test_existing_success_never_republishes_the_same_record(monkeypatch, tmp_path):
    calls = []

    class _Publisher(ResultPublisher):
        name = "remote"

        def publish(self, record):
            calls.append(record.run.run_id)
            return {}

    monkeypatch.setattr(fin, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_Publisher())
    path = _record_path(tmp_path, rec)
    orch._publish(path, tmp_path, _Publisher(), debug=False)

    assert len(calls) == 1
    assert _receipts(tmp_path)[-1]["code"] == "already_published"


def test_a_malicious_name_property_is_isolated_and_normalized(monkeypatch, tmp_path):
    calls = []

    class _Publisher(ResultPublisher):
        @property
        def name(self):
            raise RuntimeError("name exploded")

        def publish(self, record):
            calls.append(record)
            return {}

    monkeypatch.setattr(fin, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_Publisher())

    assert calls == []
    assert rec.run.stages["PUBLISH"] == "skipped"
    receipt = _receipts(tmp_path)[-1]
    assert receipt["state"] == "failed"
    assert receipt["publisher_called"] is False
    assert receipt["publisher"].replace("-", "").replace("_", "").isalnum()
    assert receipt["code"] == "publisher_name_invalid"


def test_non_json_detail_is_indeterminate_after_remote_success(monkeypatch, tmp_path):
    calls = []

    class _Publisher(ResultPublisher):
        name = "remote"

        def publish(self, record):
            calls.append(record.run.run_id)
            return {"bad": object()}

    monkeypatch.setattr(fin, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_Publisher())

    pending, terminal = _receipts(tmp_path)
    assert pending["state"] == "pending"
    assert terminal["state"] == "indeterminate"
    assert terminal["code"] == "publish_detail_invalid"

    orch._publish(_record_path(tmp_path, rec), tmp_path, _Publisher(), debug=False)
    assert len(calls) == 1
    assert _receipts(tmp_path)[-1]["code"] == "prior_attempt_indeterminate"


def test_hostile_detail_serialization_is_isolated(monkeypatch, tmp_path):
    class _HostileDict(dict):
        def items(self):
            raise RuntimeError("items exploded")

    class _Publisher(ResultPublisher):
        name = "remote"

        def publish(self, record):
            return _HostileDict()

    monkeypatch.setattr(fin, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_Publisher())

    assert rec.run.stages["PUBLISH"] == "skipped"
    terminal = _receipts(tmp_path)[-1]
    assert terminal["state"] == "indeterminate"
    assert terminal["code"] == "publish_detail_invalid"


def test_publisher_file_tampering_is_detected_after_the_remote_call(monkeypatch, tmp_path):
    original_bytes = {}

    class _Publisher(ResultPublisher):
        name = "tamperer"

        def publish(self, record):
            path = _record_path(tmp_path, record)
            original_bytes["record"] = path.read_bytes()
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["status"] = "failed"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return {}

    monkeypatch.setattr(fin, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_Publisher())

    assert rec.status == "completed"
    terminal = _receipts(tmp_path)[-1]
    assert terminal["state"] == "indeterminate"
    assert terminal["code"] == "publisher_tampering_restored"
    assert _record_path(tmp_path, rec).read_bytes() == original_bytes["record"]
    assert _persisted(tmp_path, rec) == rec.to_dict()
    assert record_digest(_persisted(tmp_path, rec)) == rec.fingerprints.record_digest


def test_publisher_sidecar_tampering_is_restored_atomically(monkeypatch, tmp_path):
    class _Publisher(ResultPublisher):
        name = "sidecar-tamperer"

        def publish(self, record):
            sidecar.write_bytes(b"tampered")
            return {}

    monkeypatch.setattr(fin, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path)
    path = _record_path(tmp_path, rec)
    payload = _persisted(tmp_path, rec)
    sidecar = path.parent / "evidence.bin"
    original_sidecar = b"trusted-sidecar"
    sidecar.write_bytes(original_sidecar)
    payload["series"] = {
        "$parquet": sidecar.relative_to(tmp_path).as_posix(),
        "sha256": "test-only",
        "rows": 1,
    }
    payload["fingerprints"]["record_digest"] = record_digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(
        "clousight_bench.core.publish.validate_sidecar",
        lambda results_dir, data: (sidecar, None),
    )
    orch._publish(path, tmp_path, _Publisher(), debug=False)

    assert sidecar.read_bytes() == original_sidecar
    terminal = _receipts(tmp_path)[-1]
    assert terminal["state"] == "indeterminate"
    assert terminal["code"] == "publisher_tampering_restored"


def test_first_receipt_creation_fsyncs_file_and_parent_directory(monkeypatch, tmp_path):
    fsynced_modes = []
    real_fsync = os.fsync

    def recording_fsync(fd):
        fsynced_modes.append(stat.S_IFMT(os.fstat(fd).st_mode))
        real_fsync(fd)

    monkeypatch.setattr("clousight_bench.core.publish.os.fsync", recording_fsync)
    append_receipt(tmp_path, {"state": "pending", "publisher": "safe"})

    assert stat.S_IFDIR in fsynced_modes
    assert stat.S_IFREG in fsynced_modes


def test_receipt_append_is_private_durable_and_thread_safe(tmp_path):
    receipt = {"state": "pending", "idempotency_key": "key", "publisher": "safe"}

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: append_receipt(tmp_path, {**receipt, "n": index}), range(40)))

    path = tmp_path / RECEIPTS_FILE
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 40
    assert sorted(json.loads(line)["n"] for line in lines) == list(range(40))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("publisher_called", "should_publish"),
    [(False, True), (True, False)],
)
def test_only_pre_call_failed_receipts_allow_retry(tmp_path, publisher_called, should_publish):
    key = "sha256:stable"
    append_receipt(
        tmp_path,
        {
            "state": "failed",
            "publisher_called": publisher_called,
            "idempotency_key": key,
            "attempt_id": "old",
        },
    )

    reservation = begin_publish_attempt(
        tmp_path,
        run_id="run-1",
        publisher="safe",
        idempotency_key=key,
        record_digest="sha256:record",
        at="2026-07-26T00:00:00Z",
    )

    assert reservation.should_publish is should_publish
    if not should_publish:
        assert _receipts(tmp_path)[-1]["code"] == "prior_called_failure"


def test_result_publisher_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        ResultPublisher()
