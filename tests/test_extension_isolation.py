"""A third-party extension must never be able to change the core verdict."""
import json

import pytest

import clousight_bench.core.orchestrator as orch
from clousight_bench.core.plugin import ResultEnricher
from clousight_bench.core.publish import RECEIPTS_FILE, ResultPublisher
from clousight_bench.core.record import ResultRecord
from clousight_bench.core.schema import RunSpec

_SPEC = RunSpec(
    "agent-runtime",
    "T1.3",
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
    path = (
        tmp_path
        / "agent-runtime"
        / "local-sim"
        / f"T1.3-{record.run.run_id}.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_failing_enricher_does_not_change_the_core_status(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "load_enrichers", lambda: [_Boom()])
    rec = orch.execute(_SPEC, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["recovery_mode"]["value"] == "auto-retry"
    assert rec.run.stages["ENRICH"] == "failed"
    enrich_errors = [e for e in rec.errors if e["stage"] == "ENRICH"]
    assert len(enrich_errors) == 1
    assert enrich_errors[0]["code"] == "enricher_failed"
    assert enrich_errors[0]["type"] == "RuntimeError"
    assert "boom" in enrich_errors[0]["message"]


def test_a_failing_enricher_does_not_block_the_others(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "load_enrichers", lambda: [_Boom(), _Good()])
    rec = orch.execute(_SPEC, results_dir=tmp_path)
    assert rec.extensions["good"] == {"applied": True}
    assert rec.run.stages["ENRICH"] == "failed"


def test_an_enricher_returning_the_wrong_type_is_rejected(monkeypatch, tmp_path):
    class _Wrong(ResultEnricher):
        name = "wrong"

        def enrich(self, record):
            return {"not": "a record"}

    monkeypatch.setattr(orch, "load_enrichers", lambda: [_Wrong()])
    rec = orch.execute(_SPEC, results_dir=tmp_path)
    assert isinstance(rec, ResultRecord)
    assert rec.status == "completed"
    assert [e["code"] for e in rec.errors if e["stage"] == "ENRICH"] == [
        "enricher_failed"
    ]


def test_publish_is_off_unless_a_publisher_is_injected(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path)
    assert rec.run.stages["PUBLISH"] == "skipped"
    assert not (tmp_path / RECEIPTS_FILE).exists()


def test_a_failing_publisher_writes_a_receipt_and_leaves_the_record_alone(
    monkeypatch, tmp_path
):
    class _BadPublisher(ResultPublisher):
        name = "bad"

        def publish(self, record):
            raise ConnectionError("data service unreachable")

    monkeypatch.setattr(orch, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_BadPublisher())

    assert rec.status == "completed"
    assert rec.run.stages["PUBLISH"] == "failed"
    receipts = [
        json.loads(line)
        for line in (tmp_path / RECEIPTS_FILE).read_text(encoding="utf-8").splitlines()
    ]
    assert receipts[-1]["ok"] is False
    assert receipts[-1]["publisher"] == "bad"
    assert receipts[-1]["run_id"] == rec.run.run_id
    assert receipts[-1]["type"] == "ConnectionError"

    persisted = _persisted(tmp_path, rec)
    assert persisted["status"] == "completed"
    assert [e["stage"] for e in persisted["errors"]] == []


def test_a_successful_publisher_writes_an_ok_receipt(monkeypatch, tmp_path):
    class _GoodPublisher(ResultPublisher):
        name = "good"

        def publish(self, record):
            return {"remote_id": "abc"}

    monkeypatch.setattr(orch, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_GoodPublisher())
    assert rec.run.stages["PUBLISH"] == "ok"
    receipt = json.loads((tmp_path / RECEIPTS_FILE).read_text(encoding="utf-8").strip())
    assert receipt["ok"] is True
    assert receipt["detail"] == {"remote_id": "abc"}


def test_publisher_receives_the_durable_record_and_cannot_mutate_core(
    monkeypatch, tmp_path
):
    seen = {}

    class _MutatingPublisher(ResultPublisher):
        name = "mutating"

        def publish(self, record):
            seen["record"] = record.to_dict()
            record.status = "failed"
            record.measurements.clear()
            return {"remote_id": "abc"}

    monkeypatch.setattr(orch, "load_enrichers", list)
    rec = orch.execute(_SPEC, results_dir=tmp_path, publisher=_MutatingPublisher())
    persisted = _persisted(tmp_path, rec)

    assert seen["record"] == persisted
    assert seen["record"]["run"]["stages"]["PERSIST"] == "ok"
    assert rec.status == "completed"
    assert rec.measurements["recovery_mode"]["value"] == "auto-retry"
    assert persisted["status"] == "completed"


def test_receipts_redact_secrets_and_machine_identity(monkeypatch, tmp_path):
    class _LeakyPublisher(ResultPublisher):
        name = "leaky"

        def publish(self, record):
            return {
                "api_token": "super-secret",
                "remote_path": "/home/build-user/results",
            }

    monkeypatch.setattr(orch, "load_enrichers", list)
    monkeypatch.setattr(
        "clousight_bench.core.publish.identity_values", lambda: ("build-user",)
    )
    orch.execute(_SPEC, results_dir=tmp_path, publisher=_LeakyPublisher())
    text = (tmp_path / RECEIPTS_FILE).read_text(encoding="utf-8")

    assert "super-secret" not in text
    assert "build-user" not in text
    assert "<redacted>" in text


def test_result_publisher_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        ResultPublisher()
