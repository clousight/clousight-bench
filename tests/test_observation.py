"""Observations stay raw; scoring conclusions live only in TaskResult."""

import pytest

from clousight_bench.core.canonical import CanonicalJSONError
from clousight_bench.core.observation import (
    Finding,
    Measurement,
    ObservationBundle,
    ObservationError,
    TaskExecutionError,
    TaskResult,
    collect,
)


def test_measurement_requires_value_unit_and_evidence():
    m = Measurement(value=12.5, unit="ms", evidence="C")
    assert m.to_dict() == {"value": 12.5, "unit": "ms", "evidence": "C"}


def test_measurement_emits_optional_fields_only_when_set():
    m = Measurement(
        value=1,
        unit="ms",
        evidence="C",
        aggregation="p99",
        sample_count=100,
        notes="warm",
    )
    assert m.to_dict() == {
        "value": 1,
        "unit": "ms",
        "evidence": "C",
        "aggregation": "p99",
        "sample_count": 100,
        "notes": "warm",
    }


def test_measurement_rejects_an_unknown_evidence_layer():
    with pytest.raises(ObservationError, match="evidence"):
        Measurement(value=1, unit="", evidence="Z")


def test_finding_requires_a_stable_code_and_known_severity():
    f = Finding(
        code="agent_runtime.state_ephemeral",
        severity="warning",
        summary="state lost on resume",
        evidence="C",
        details={"n": 1},
    )
    assert f.to_dict()["code"] == "agent_runtime.state_ephemeral"
    with pytest.raises(ObservationError, match="code"):
        Finding(code="", severity="warning", summary="s", evidence="C")
    with pytest.raises(ObservationError, match="severity"):
        Finding(code="c", severity="fatal", summary="s", evidence="C")


def test_collect_accepts_a_well_formed_bundle_and_returns_it():
    bundle = ObservationBundle(
        observations={"attempts": [{"ok": True}]},
        series={"latency_ms": [[1, 10.0]]},
        artifacts=[
            {
                "kind": "trace",
                "path": "t.json",
                "media": "application/json",
                "sha256": "sha256:ab",
            }
        ],
    )
    assert collect(bundle) is bundle


def test_collect_rejects_a_non_bundle():
    with pytest.raises(ObservationError, match="ObservationBundle"):
        collect({"observations": {}})


def test_collect_rejects_non_finite_numbers():
    with pytest.raises(CanonicalJSONError):
        collect(ObservationBundle(observations={"v": float("nan")}))


def test_collect_rejects_malformed_series_points():
    with pytest.raises(ObservationError, match="latency_ms"):
        collect(ObservationBundle(series={"latency_ms": [[1, 2, 3]]}))


def test_collect_rejects_artifacts_without_a_pointer_or_digest():
    with pytest.raises(ObservationError, match="sha256"):
        collect(ObservationBundle(artifacts=[{"kind": "t", "path": "p", "media": "m"}]))
    with pytest.raises(ObservationError, match="pointer"):
        collect(ObservationBundle(artifacts=[{"kind": "t", "media": "m", "sha256": "sha256:ab"}]))


def test_task_result_defaults_are_empty_and_supported():
    result = TaskResult()
    assert result.measurements == {}
    assert result.findings == []
    assert result.unsupported is False


def test_task_execution_error_carries_partial_observations():
    bundle = ObservationBundle(observations={"attempts": [{"ok": False}]})
    error = TaskExecutionError(
        "tool failed",
        observations=bundle,
        code="tool_failed",
        retryable=True,
    )
    assert error.observations is bundle
    assert error.code == "tool_failed"
    assert error.retryable is True
