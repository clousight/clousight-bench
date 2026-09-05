"""The lifecycle must never leak a resource and never lose an observation."""

import json

import pytest

import clousight_bench.core.finalize as fin
import clousight_bench.core.orchestrator as orch
from clousight_bench.core.observation import (
    Measurement,
    ObservationBundle,
    TaskExecutionError,
    TaskResult,
)
from clousight_bench.core.plugin import DomainPack, ProviderAdapter
from clousight_bench.core.record import Provenance
from clousight_bench.core.schema import RunSpec

CALLS: list[str] = []


class _Adapter(ProviderAdapter):
    name = "fake"
    status = "reference"
    provider = None
    setup_raises = False
    teardown_raises = False

    def setup(self) -> None:
        CALLS.append("setup")
        if type(self).setup_raises:
            raise RuntimeError("setup blew up after allocating half a cluster")

    def teardown(self) -> None:
        CALLS.append("teardown")
        if type(self).teardown_raises:
            raise OSError("teardown could not reach the control plane")


class _Task:
    """Runner-shaped stub (the SuiteRunner duck type) driving the stage machine."""

    task_id = "suite:TX"
    title = "fake"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions: tuple = ()
    capability_tags: tuple = ()
    execute_raises = False
    score_raises = False

    def config(self, params):
        return {"task_id": self.task_id}

    def workload_identity(self, params):
        return {"workload": "", "workload_version": "", "assets": []}

    def provenance(self):
        return Provenance()

    def environment_facts(self, adapter, params):
        return {"fake": True}

    def execute(self, adapter, params):
        CALLS.append("execute")
        if type(self).execute_raises:
            raise ConnectionError("the runtime dropped the session")
        return ObservationBundle(observations={"hits": 3}, series={"latency_ms": [[1, 10.0]]})

    def score(self, observations):
        CALLS.append("score")
        if type(self).score_raises:
            raise ZeroDivisionError("scorer bug")
        return TaskResult(
            measurements={
                "hits": Measurement(
                    value=observations.observations["hits"],
                    unit="count",
                    reproducibility_class="deterministic",
                )
            },
            notes="ok",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )


class _Domain(DomainPack):
    domain = "fake-domain"

    def adapters(self):
        return {"fake": _Adapter}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    CALLS.clear()
    _Adapter.setup_raises = False
    _Adapter.teardown_raises = False
    _Task.execute_raises = False
    _Task.score_raises = False
    monkeypatch.setattr(orch, "get_domain", lambda name: _Domain())
    monkeypatch.setattr(orch, "_resolve_benchmark", lambda spec, results_dir, trace_id="": _Task())
    monkeypatch.setattr(fin, "load_enrichers", list)


def _run(tmp_path, **kwargs):
    return orch.execute(RunSpec("fake-domain", "suite:TX", "fake"), results_dir=tmp_path, **kwargs)


def test_happy_path_produces_a_completed_0_3_record(tmp_path):
    record = _run(tmp_path)
    assert record.schema_version == "0.4"
    assert record.status == "completed"
    assert record.errors == []
    assert record.measurements["hits"] == {
        "value": 3,
        "unit": "count",
        "reproducibility_class": "deterministic",
        "official": True,
    }
    assert record.observations == {"hits": 3}
    assert record.identity.task_revision == "1"
    assert record.identity.adapter_status == "reference"
    assert record.environment.mode == "local"
    assert record.environment.facts == {"fake": True}
    assert record.fingerprints.benchmark.startswith("sha256:")
    assert record.fingerprints.record_digest.startswith("sha256:")
    assert record.run.stages["TEARDOWN"] == "ok"
    assert CALLS == ["setup", "execute", "teardown", "score"]


def test_partial_setup_failure_still_tears_down(tmp_path):
    _Adapter.setup_raises = True
    record = _run(tmp_path)
    assert CALLS == ["setup", "teardown"]
    assert record.status == "failed"
    assert record.run.stages["SETUP"] == "failed"
    assert record.run.stages["TEARDOWN"] == "ok"
    assert [e["stage"] for e in record.errors] == ["SETUP"]


def test_teardown_error_does_not_overwrite_the_execute_error(tmp_path):
    _Task.execute_raises = True
    _Adapter.teardown_raises = True
    record = _run(tmp_path)
    assert record.status == "failed"
    assert [e["stage"] for e in record.errors] == ["EXECUTE", "TEARDOWN"]
    assert record.errors[0]["type"] == "ConnectionError"
    assert record.errors[1]["type"] == "OSError"


def test_teardown_error_alone_keeps_the_run_completed(tmp_path):
    _Adapter.teardown_raises = True
    record = _run(tmp_path)
    assert record.status == "completed"
    assert [e["stage"] for e in record.errors] == ["TEARDOWN"]
    assert record.measurements["hits"]["value"] == 3


def test_score_failure_keeps_the_observations(tmp_path, monkeypatch):
    # Keep the series inline so this test reads the evidence, not a pointer to it.
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    _Task.score_raises = True
    record = _run(tmp_path)
    assert record.status == "failed"
    assert record.observations == {"hits": 3}
    assert record.series == {"latency_ms": [[1, 10.0]]}
    assert record.measurements == {}
    assert [e["stage"] for e in record.errors] == ["SCORE"]
    assert record.errors[0]["type"] == "ZeroDivisionError"


def test_execute_failure_keeps_partial_observations_carried_by_the_error(
    tmp_path,
    monkeypatch,
):
    partial = ObservationBundle(observations={"attempts": [{"ok": False}]})
    monkeypatch.setattr(
        _Task,
        "execute",
        lambda self, adapter, params: (_ for _ in ()).throw(
            TaskExecutionError(
                "tool failed",
                observations=partial,
                code="tool_failed",
                retryable=True,
            )
        ),
    )
    record = _run(tmp_path)
    assert record.status == "failed"
    assert record.observations == partial.observations
    assert record.errors[0]["stage"] == "EXECUTE"
    assert record.errors[0]["code"] == "tool_failed"
    assert record.errors[0]["retryable"] is True


def test_records_never_carry_a_traceback(tmp_path):
    _Task.execute_raises = True
    payload = json.dumps(_run(tmp_path).to_dict())
    assert "Traceback" not in payload
    assert "test_lifecycle.py" not in payload


def test_debug_writes_the_traceback_to_a_local_log_only(tmp_path):
    _Task.execute_raises = True
    record = _run(tmp_path, debug=True)
    log = tmp_path / "debug" / f"{record.run.run_id}.log"
    assert log.is_file()
    assert "Traceback" in log.read_text(encoding="utf-8")
    assert "Traceback" not in json.dumps(record.to_dict())


def test_unsupported_capability_becomes_an_unsupported_status(tmp_path, monkeypatch):
    monkeypatch.setattr(
        _Task,
        "score",
        lambda self, observations: TaskResult(unsupported=True, notes="no API"),
    )
    assert _run(tmp_path).status == "unsupported"


def test_resolve_and_validate_errors_write_no_record(tmp_path):
    from clousight_bench.core.errors import UnknownTaskError

    with pytest.raises(UnknownTaskError):
        orch.execute(RunSpec("fake-domain", "NOPE", "fake"), results_dir=tmp_path)
    assert list(tmp_path.rglob("*.json")) == []


def test_stages_omit_resolve_but_keep_the_publish_denial(tmp_path):
    """``run.stages`` records the stages whose outcome this record depends on.

    A RESOLVE failure raises before any record exists, so ``RESOLVE: ok`` is a
    constant in every persisted record — zero information. PUBLISH's ``skipped``
    is the opposite: an explicit, durable denial that a publisher ever touched
    this sealed record, so it stays.
    """
    record = _run(tmp_path)
    assert "RESOLVE" not in record.run.stages
    assert record.run.stages["PUBLISH"] == "skipped"
