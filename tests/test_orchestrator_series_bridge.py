"""ObservationBundle series/artifacts must flow through the lifecycle into the record."""

import clousight_bench.core.orchestrator as orch
from clousight_bench.core.observation import Measurement, ObservationBundle, TaskResult
from clousight_bench.core.plugin import DomainPack, ProviderAdapter
from clousight_bench.core.record import Provenance
from clousight_bench.core.schema import RunSpec


class _Adapter(ProviderAdapter):
    name = "fake"
    status = "reference"


class _Task:
    """Runner-shaped stub (the SuiteRunner duck type)."""

    task_id = "suite:TX"
    title = ""
    task_revision = "0"
    scorer_revision = "0"
    required_permissions: tuple = ()
    capability_tags: tuple = ()

    def config(self, params):
        return {}

    def environment_facts(self, adapter, params):
        return {}

    def workload_identity(self, params):
        return {"workload": "", "workload_version": "", "assets": []}

    def provenance(self):
        return Provenance()

    def execute(self, adapter, params):
        return ObservationBundle(
            series={"latency_ms": [[1, 10.0]]},
            artifacts=[{"kind": "trace", "path": "p", "media": "m", "sha256": "sha256:x"}],
        )

    def score(self, observations):
        return TaskResult(
            measurements={"p99_ms": Measurement(value=1, unit="ms", reproducibility_class="environmental")}
        )


class _Domain(DomainPack):
    domain = "fake-domain"

    def adapters(self):
        return {"fake": _Adapter}


def test_series_and_artifacts_reach_record(monkeypatch, tmp_path):
    # The store may externalize series to Parquet and leave a pointer behind;
    # this test is about the bridge, so keep the values where we can read them.
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    monkeypatch.setattr(orch, "get_domain", lambda name: _Domain())
    monkeypatch.setattr(orch, "_resolve_benchmark", lambda spec, results_dir, trace_id="": _Task())
    rec = orch.execute(RunSpec("fake-domain", "suite:TX", "fake"), results_dir=tmp_path, enrich=False)
    assert rec.series == {"latency_ms": [[1, 10.0]]}
    assert rec.artifacts[0]["kind"] == "trace"


def test_series_are_externalized_to_a_pointer_on_the_returned_record(monkeypatch, tmp_path):
    from clousight_bench.core.store import STORE_AVAILABLE

    if not STORE_AVAILABLE:
        return
    monkeypatch.setattr(orch, "get_domain", lambda name: _Domain())
    monkeypatch.setattr(orch, "_resolve_benchmark", lambda spec, results_dir, trace_id="": _Task())
    rec = orch.execute(RunSpec("fake-domain", "suite:TX", "fake"), results_dir=tmp_path, enrich=False)
    assert rec.series["rows"] == 1
    assert (tmp_path / rec.series["$parquet"]).is_file()
