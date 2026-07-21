"""Reproducibility-contract tests: config_hash determinism + evidence-layer guard."""
import pytest

from clousight_bench.core.schema import ResultRecord, RunSpec, config_hash, utc_now


def test_config_hash_is_deterministic_and_order_independent():
    a = {"x": 1, "y": [1, 2], "z": {"b": 2, "a": 1}}
    b = {"z": {"a": 1, "b": 2}, "y": [1, 2], "x": 1}
    assert config_hash(a) == config_hash(b)
    assert config_hash(a).startswith("sha256:")


def test_config_hash_changes_with_content():
    assert config_hash({"x": 1}) != config_hash({"x": 2})


def test_result_record_rejects_bad_evidence_layer():
    with pytest.raises(ValueError):
        ResultRecord(
            domain="d", task_id="t", platform="p", run_id="r",
            started_at=utc_now(), finished_at=utc_now(),
            config_hash="sha256:x", evidence_layer="Z", metrics={},
        )


def test_result_record_roundtrip():
    rec = ResultRecord(
        domain="agent-runtime", task_id="T1.3", platform="local-sim", run_id="r1",
        started_at=utc_now(), finished_at=utc_now(),
        config_hash="sha256:abc", evidence_layer="C", metrics={"a": 1},
    )
    again = ResultRecord.from_dict(rec.to_dict())
    assert again.to_dict() == rec.to_dict()
    assert again.runner_version == rec.runner_version


def test_runspec_to_dict():
    spec = RunSpec(domain="d", task_id="t", platform="p", target={"k": "v"}, params={"n": 1})
    assert spec.to_dict()["target"] == {"k": "v"}
