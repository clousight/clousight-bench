"""ObservationBundle series/artifacts must flow through the lifecycle into the record."""
import clousight_bench.core.orchestrator as orch
from clousight_bench.core.observation import Measurement, ObservationBundle, TaskResult
from clousight_bench.core.plugin import DomainPack, ProviderAdapter, Task
from clousight_bench.core.schema import RunSpec


class _Adapter(ProviderAdapter):
    name = "fake"
    status = "reference"


class _Task(Task):
    task_id = "TX"
    evidence_layer = "C"

    def config(self, params):
        return {}

    def execute(self, adapter, params):
        return ObservationBundle(
            series={"latency_ms": [[1, 10.0]]},
            artifacts=[{"kind": "trace", "path": "p", "media": "m", "sha256": "sha256:x"}],
        )

    def score(self, observations):
        return TaskResult(
            measurements={"p99_ms": Measurement(value=1, unit="ms", evidence="C")}
        )


class _Domain(DomainPack):
    domain = "fake-domain"

    def tasks(self):
        return {"TX": _Task}

    def adapters(self):
        return {"fake": _Adapter}


def test_series_and_artifacts_reach_record(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "get_domain", lambda name: _Domain())
    rec = orch.execute(
        RunSpec("fake-domain", "TX", "fake"), results_dir=tmp_path, enrich=False
    )
    assert rec.series == {"latency_ms": [[1, 10.0]]}
    assert rec.artifacts[0]["kind"] == "trace"
