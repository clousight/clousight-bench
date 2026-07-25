"""Task.execute/score is the contract; run() is a bridge deleted at cutover."""

import pytest

from clousight_bench.core.observation import (
    Finding,
    Measurement,
    ObservationBundle,
    TaskResult,
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
    task = _Unimplemented()
    with pytest.raises(NotImplementedError, match="execute"):
        task.execute(_Adapter(), {})
    with pytest.raises(NotImplementedError, match="score"):
        task.score(ObservationBundle())


def test_bridge_run_composes_execute_collect_and_score():
    out = _Good().run(_Adapter(), {})
    assert out.metrics == {"hits": 2}
    assert out.evidence_layer == "C"
    assert out.ok is True
    assert out.raw == {"hits": 2}
    assert out.series == {"latency_ms": [[1, 10.0]]}
    assert out.artifacts[0]["kind"] == "trace"
    assert out.notes == "hits=2"


def test_bridge_run_reports_not_ok_on_a_critical_finding(monkeypatch):
    task = _Good()
    monkeypatch.setattr(
        task,
        "execute",
        lambda adapter, params: ObservationBundle(observations={"hits": 0}),
    )
    assert task.run(_Adapter(), {}).ok is False
