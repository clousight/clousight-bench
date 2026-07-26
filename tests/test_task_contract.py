"""Task.execute/score is the whole contract a concrete task must implement."""

import pytest

from clousight_bench.core.observation import (
    Finding,
    Measurement,
    ObservationBundle,
    TaskResult,
    collect,
)
from clousight_bench.core.plugin import ProviderAdapter, Task


class _Adapter(ProviderAdapter):
    name = "fake"


class _Good(Task):
    task_id = "TX"
    evidence_layer = "C"
    task_revision = "3"
    scorer_revision = "4"

    def config(self, params):
        return {"task_id": self.task_id}

    def execute(self, adapter, params):
        return ObservationBundle(
            observations={"hits": 2},
            series={"latency_ms": [[1, 10.0]]},
            artifacts=[
                {
                    "kind": "trace",
                    "path": "t",
                    "media": "m",
                    "sha256": "sha256:a",
                }
            ],
        )

    def score(self, observations):
        hits = observations.observations["hits"]
        return TaskResult(
            measurements={
                "hits": Measurement(value=hits, unit="count", evidence="C")
            },
            findings=[]
            if hits
            else [
                Finding(
                    code="tx.no_hits",
                    severity="critical",
                    summary="nothing observed",
                    evidence="C",
                )
            ],
            notes=f"hits={hits}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )


class _Unimplemented(Task):
    task_id = "TY"

    def config(self, params):
        return {}


def test_default_revisions_are_zero():
    assert Task.task_revision == "0"
    assert Task.scorer_revision == "0"


def test_default_environment_facts_and_workload_identity_are_empty():
    task = _Good()
    assert task.environment_facts(_Adapter(), {}) == {}
    assert task.workload_identity({}) == {
        "workload": "",
        "workload_version": "",
        "assets": [],
    }


def test_execute_and_score_are_required_of_a_concrete_task():
    with pytest.raises(TypeError, match="execute"):
        _Unimplemented()


def test_a_task_composes_execute_collect_and_score(monkeypatch):
    task = _Good()
    bundle = collect(task.execute(_Adapter(), {}))
    result = task.score(bundle)

    assert bundle.observations == {"hits": 2}
    assert bundle.series == {"latency_ms": [[1, 10.0]]}
    assert bundle.artifacts[0]["kind"] == "trace"
    assert result.measurements["hits"].to_dict() == {
        "value": 2,
        "unit": "count",
        "evidence": "C",
    }
    assert result.findings == []
    assert result.notes == "hits=2"


def test_score_reports_a_critical_finding_when_nothing_was_observed():
    result = _Good().score(ObservationBundle(observations={"hits": 0}))
    assert [f.code for f in result.findings] == ["tx.no_hits"]
    assert result.findings[0].severity == "critical"
