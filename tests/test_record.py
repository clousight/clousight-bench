"""ResultRecord 0.3: the shape every reader and every plugin agrees on."""

import pytest

from clousight_bench.core.record import (
    SCHEMA_VERSION,
    Environment,
    Fingerprints,
    Identity,
    Provenance,
    RecordError,
    ResultRecord,
    RunInfo,
    StageError,
)


def _record(**overrides):
    base = dict(
        run=RunInfo(
            run_id="run-1",
            started_at="2026-07-25T00:00:00Z",
            finished_at="2026-07-25T00:00:01Z",
            stages={"EXECUTE": "ok"},
        ),
        identity=Identity(
            domain="agent-runtime",
            task_id="T1.3",
            task_revision="2",
            scorer_revision="2",
            adapter="local-sim",
            adapter_status="reference",
            core_version="0.2.0",
        ),
        environment=Environment(
            region="",
            mode="local",
            python_version="3.12.0",
            os_name="Linux",
        ),
        fingerprints=Fingerprints(
            benchmark="sha256:a",
            environment="sha256:b",
            implementation="sha256:c",
        ),
        status="completed",
    )
    base.update(overrides)
    return ResultRecord(**base)


def test_top_level_keys_are_the_fixed_contract():
    assert set(_record().to_dict()) == {
        "schema_version",
        "run",
        "identity",
        "environment",
        "fingerprints",
        "provenance",
        "measurements",
        "findings",
        "observations",
        "series",
        "artifacts",
        "extensions",
        "errors",
        "status",
    }


def test_legacy_fields_are_gone():
    payload = _record().to_dict()
    for gone in ("ok", "metrics", "evidence_layer", "config_hash", "raw", "notes"):
        assert gone not in payload


def test_status_must_be_one_of_the_four_values():
    with pytest.raises(RecordError, match="status"):
        _record(status="green")


def test_mode_must_be_a_known_value():
    with pytest.raises(RecordError, match="mode"):
        _record(
            environment=Environment(
                region="",
                mode="hybrid",
                python_version="3.12.0",
                os_name="Linux",
            )
        )


def test_stage_error_carries_the_mandatory_fields():
    err = StageError(
        stage="EXECUTE",
        code="tool_plan_failed",
        type="ConnectionError",
        message="boom",
        retryable=True,
    )
    assert err.to_dict() == {
        "stage": "EXECUTE",
        "code": "tool_plan_failed",
        "type": "ConnectionError",
        "message": "boom",
        "retryable": True,
    }


def test_stage_error_rejects_an_unknown_stage():
    with pytest.raises(RecordError, match="stage"):
        StageError(stage="LAUNCH", code="c", type="T", message="m")


def test_round_trip_is_lossless():
    record = _record(
        measurements={"p99_ms": {"value": 9, "unit": "ms", "reproducibility_class": "environmental"}},
        findings=[
            {
                "code": "x.y",
                "severity": "warning",
                "summary": "s",
                "details": {},
            }
        ],
        observations={"attempts": [1, 2]},
        series={"latency_ms": [[1, 10.0]]},
        artifacts=[
            {
                "kind": "trace",
                "path": "t",
                "media": "m",
                "sha256": "sha256:a",
            }
        ],
        extensions={"core": {"notes": "n"}},
        errors=[
            StageError(
                stage="TEARDOWN",
                code="teardown_failed",
                type="OSError",
                message="m",
            ).to_dict()
        ],
    )
    again = ResultRecord.from_dict(record.to_dict())
    assert again.to_dict() == record.to_dict()
    assert again.identity.task_revision == "2"
    assert again.run.stages == {"EXECUTE": "ok"}


def test_observation_artifact_pointer_survives_round_trip():
    pointer = {"trace": {"$artifact": "trace.jsonl"}}
    record = _record(
        observations=pointer,
        artifacts=[{"kind": "trace", "path": "trace.jsonl"}],
    )

    assert ResultRecord.from_dict(record.to_dict()).observations == pointer


def test_from_dict_rejects_a_legacy_record_with_schema_version_error():
    with pytest.raises(RecordError, match="no migration path"):
        ResultRecord.from_dict({"schema_version": "1.0", "domain": "d"})


def test_schema_version_is_0_3():
    assert SCHEMA_VERSION == "0.3"


def test_provenance_defaults():
    p = Provenance()
    assert p.suite_id == ""
    assert p.suite_version == ""
    assert p.dataset_digest == ""
    assert p.unmodified is True
    assert p.evaluator_id == ""
    assert p.evaluator_official is True
    assert p.scaffold == ""
    assert p.division == ""


def test_provenance_is_empty_when_all_defaults():
    assert Provenance().is_empty() is True
    assert Provenance(suite_id="x").is_empty() is False


def test_provenance_roundtrip():
    p = Provenance(
        suite_id="s1", suite_version="v2", evaluator_id="e3", unmodified=False, evaluator_official=False
    )
    p2 = Provenance.from_dict(p.to_dict())
    assert p2 == p


def test_provenance_from_dict_tolerant():
    p = Provenance.from_dict({})
    assert p == Provenance()


def test_record_carries_empty_provenance_by_default():
    record = _record()
    assert record.provenance == Provenance()
    assert "provenance" in record.to_dict()


def test_record_provenance_roundtrips():
    record = _record()
    record2 = ResultRecord.from_dict(record.to_dict())
    assert record2.provenance == Provenance()


def test_from_dict_rejects_0_2_payload():
    with pytest.raises(RecordError, match="no migration path"):
        ResultRecord.from_dict({"schema_version": "0.2"})
