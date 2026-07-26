"""Every failure outside the task itself must still land in an auditable record.

The lifecycle owns three kinds of failure: a bad *request* (raised, no record),
broken *plugin* code (recorded, nothing provisioned) and a failing *platform*
(recorded, provisioned and torn down). None of them may lose the record.
"""
import getpass
import json
from copy import deepcopy

import pytest

import clousight_bench.core.orchestrator as orch
from clousight_bench.core.observation import (
    Measurement,
    ObservationBundle,
    TaskExecutionError,
    TaskResult,
)
from clousight_bench.core.plugin import DomainPack, ProviderAdapter, ResultEnricher, Task
from clousight_bench.core.schema import RunSpec

CALLS: list[str] = []


class _Adapter(ProviderAdapter):
    name = "fake"
    status = "reference"
    provider = None

    def setup(self) -> None:
        CALLS.append("setup")

    def teardown(self) -> None:
        CALLS.append("teardown")


class _Task(Task):
    task_id = "TX"
    title = "fake"
    evidence_layer = "C"
    task_revision = "1"
    scorer_revision = "1"

    def config(self, params):
        CALLS.append("config")
        return {"task_id": self.task_id}

    def environment_facts(self, adapter, params):
        return {"fake": True}

    def execute(self, adapter, params):
        CALLS.append("execute")
        return ObservationBundle(observations={"hits": 3})

    def score(self, observations):
        return TaskResult(
            measurements={
                "hits": Measurement(
                    value=observations.observations["hits"], unit="count", evidence="C"
                )
            },
            notes="ok",
        )


class _Domain(DomainPack):
    domain = "fake-domain"

    def tasks(self):
        return {"TX": _Task}

    def adapters(self):
        return {"fake": _Adapter}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    CALLS.clear()
    monkeypatch.setattr(orch, "get_domain", lambda name: _Domain())
    monkeypatch.setattr(orch, "load_enrichers", list)


def _run(tmp_path, **kwargs):
    return orch.execute(
        RunSpec("fake-domain", "TX", "fake"), results_dir=tmp_path, **kwargs
    )


def _persisted(tmp_path) -> dict:
    files = [p for p in tmp_path.rglob("*.json")]
    assert len(files) == 1, files
    return json.loads(files[0].read_text(encoding="utf-8"))


class _Tagger(ResultEnricher):
    name = "tagger"

    def enrich(self, record):
        record.extensions["tagger"] = {"applied": True}
        return record


# --- C1: a third-party enricher may never silently swallow a result ----------

def test_an_enricher_that_raises_is_recorded_and_the_record_still_persists(
    tmp_path, monkeypatch
):
    class Boom(ResultEnricher):
        name = "boom"

        def enrich(self, record):
            raise RuntimeError("commercial enricher exploded")

    monkeypatch.setattr(orch, "load_enrichers", lambda: [Boom()])
    record = _run(tmp_path)

    assert record.status == "completed"
    assert record.run.stages["ENRICH"] == "failed"
    assert [e["stage"] for e in record.errors] == ["ENRICH"]
    assert record.errors[0]["type"] == "RuntimeError"
    assert record.measurements["hits"]["value"] == 3
    assert _persisted(tmp_path)["run"]["stages"]["ENRICH"] == "failed"


def test_an_enricher_returning_none_does_not_destroy_the_record(tmp_path, monkeypatch):
    class Dropper(ResultEnricher):
        name = "dropper"

        def enrich(self, record):
            return None

    monkeypatch.setattr(orch, "load_enrichers", lambda: [Dropper()])
    record = _run(tmp_path)

    assert record is not None
    assert record.status == "completed"
    assert record.measurements["hits"]["value"] == 3
    assert record.run.stages["ENRICH"] == "failed"
    assert record.errors[0]["stage"] == "ENRICH"
    assert "None" in record.errors[0]["message"] or "record" in record.errors[0]["message"]


def test_enricher_discovery_failure_is_recorded(tmp_path, monkeypatch):
    def _boom():
        raise ImportError("broken enricher entry point")

    monkeypatch.setattr(orch, "load_enrichers", _boom)
    record = _run(tmp_path)

    assert record.status == "completed"
    assert record.run.stages["ENRICH"] == "failed"
    assert record.errors[0]["type"] == "ImportError"
    assert _persisted(tmp_path)["status"] == "completed"


def test_one_failing_enricher_does_not_stop_the_next_one(tmp_path, monkeypatch):
    class Boom(ResultEnricher):
        name = "aaa-boom"

        def enrich(self, record):
            raise RuntimeError("nope")

    monkeypatch.setattr(orch, "load_enrichers", lambda: [Boom(), _Tagger()])
    record = _run(tmp_path)

    assert record.extensions["tagger"] == {"applied": True}
    assert record.run.stages["ENRICH"] == "failed"
    assert [e["stage"] for e in record.errors] == ["ENRICH"]


def test_a_mutating_enricher_cannot_corrupt_core_fields(tmp_path, monkeypatch):
    class Malicious(ResultEnricher):
        name = "malicious"

        def enrich(self, record):
            record.errors.clear()
            record.run.stages.clear()
            record.identity.plugin_versions["forged"] = "999"
            record.measurements["hits"]["value"] = float("nan")
            return record

    monkeypatch.setattr(orch, "load_enrichers", lambda: [Malicious()])
    record = _run(tmp_path)

    assert record.status == "completed"
    assert record.measurements["hits"]["value"] == 3
    assert "forged" not in record.identity.plugin_versions
    assert record.run.stages["ENRICH"] == "failed"
    assert record.errors[-1]["stage"] == "ENRICH"
    assert record.errors[-1]["code"] == "enricher_invalid_record:malicious"


def test_each_enricher_gets_a_copy_and_a_bad_candidate_is_discarded(
    tmp_path, monkeypatch
):
    seen = {}

    class Bad(ResultEnricher):
        name = "bad"

        def enrich(self, record):
            seen["input"] = record
            record.extensions["bad"] = {"value": object()}
            return record

    baseline = deepcopy
    monkeypatch.setattr(orch, "load_enrichers", lambda: [Bad()])
    record = _run(tmp_path)

    assert seen["input"] is not record
    assert "bad" not in record.extensions
    assert baseline(record.to_dict()) == record.to_dict()
    assert record.run.stages["ENRICH"] == "failed"


def test_enricher_candidate_must_pass_record_structure_sanity(tmp_path, monkeypatch):
    class StructurallyBad(ResultEnricher):
        name = "structurally-bad"

        def enrich(self, record):
            record.measurements["broken"] = {"value": 1}
            return record

    monkeypatch.setattr(orch, "load_enrichers", lambda: [StructurallyBad()])
    record = _run(tmp_path)

    assert "broken" not in record.measurements
    assert record.run.stages["ENRICH"] == "failed"
    assert record.errors[-1]["code"] == "enricher_invalid_record:structurally-bad"


@pytest.mark.parametrize("target", ["measurements", "findings"])
def test_enricher_cannot_rewrite_core_scoring(tmp_path, monkeypatch, target):
    class ScoreRewriter(ResultEnricher):
        name = "score-rewriter"

        def enrich(self, record):
            if target == "measurements":
                record.measurements["hits"]["value"] = 999
            else:
                record.findings.append(
                    {
                        "code": "forged",
                        "severity": "info",
                        "summary": "forged",
                        "evidence": "C",
                        "details": {},
                    }
                )
            return record

    monkeypatch.setattr(orch, "load_enrichers", lambda: [ScoreRewriter()])
    record = _run(tmp_path)

    assert record.measurements["hits"]["value"] == 3
    assert record.findings == []
    assert record.run.stages["ENRICH"] == "failed"
    assert record.errors[-1]["code"] == "enricher_invalid_record:score-rewriter"


# --- I1: plugin code that crashes before provisioning is recorded, not raised -

def test_environment_facts_failure_is_recorded_and_nothing_is_provisioned(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        _Task,
        "environment_facts",
        lambda self, adapter, params: (_ for _ in ()).throw(KeyError("missing knob")),
    )
    record = _run(tmp_path)

    assert record.status == "invalid"
    assert record.run.stages["VALIDATE"] == "failed"
    assert "SETUP" not in record.run.stages
    assert CALLS.count("setup") == 0
    assert [e["stage"] for e in record.errors] == ["VALIDATE"]
    assert record.errors[0]["type"] == "KeyError"
    assert record.environment.facts == {}
    assert _persisted(tmp_path)["status"] == "invalid"


def test_workload_identity_failure_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        _Task,
        "workload_identity",
        lambda self, params: (_ for _ in ()).throw(ValueError("bad manifest")),
    )
    record = _run(tmp_path)

    assert record.status == "invalid"
    assert record.run.stages["VALIDATE"] == "failed"
    assert record.errors[0]["type"] == "ValueError"
    assert record.identity.workload == ""
    assert record.fingerprints.benchmark.startswith("sha256:")


def test_an_adapter_that_cannot_be_constructed_is_recorded(tmp_path, monkeypatch):
    def _boom(self, target=None):
        raise RuntimeError("missing SDK client")

    monkeypatch.setattr(_Adapter, "__init__", _boom)
    record = _run(tmp_path)

    assert record.status == "invalid"
    assert record.run.stages["VALIDATE"] == "failed"
    assert record.errors[0]["code"] == "adapter_init_failed"
    assert CALLS.count("setup") == 0
    assert _persisted(tmp_path)["errors"][0]["type"] == "RuntimeError"


def test_preflight_crash_is_recorded_as_a_preflight_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        _Adapter,
        "preflight",
        lambda self, task=None: (_ for _ in ()).throw(TimeoutError("control plane")),
    )
    record = _run(tmp_path)

    assert record.status == "invalid"
    assert record.run.stages["PREFLIGHT"] == "failed"
    assert "SETUP" not in record.run.stages
    assert CALLS.count("setup") == 0
    assert record.errors[0]["stage"] == "PREFLIGHT"
    assert record.errors[0]["type"] == "TimeoutError"
    assert record.errors[0]["retryable"] is True


def test_environment_facts_are_not_collected_before_preflight_passes(
    tmp_path, monkeypatch
):
    calls = []

    class FailedReport:
        ok = False
        checks = []

        def format(self):
            return "blocked"

        def summary(self):
            return "blocked"

    monkeypatch.setattr(
        _Adapter, "preflight", lambda self, task=None: calls.append("preflight") or FailedReport()
    )
    monkeypatch.setattr(
        _Task,
        "environment_facts",
        lambda self, adapter, params: calls.append("facts") or {"fake": True},
    )
    record = _run(tmp_path)

    assert calls == ["preflight"]
    assert record.status == "invalid"
    assert record.environment.facts == {}


def test_a_task_rejecting_params_stays_a_user_input_error_without_a_record(
    tmp_path, monkeypatch
):
    from clousight_bench.core.errors import UserInputError

    monkeypatch.setattr(
        _Task,
        "config",
        lambda self, params: (_ for _ in ()).throw(ValueError("rows must be > 0")),
    )
    with pytest.raises(UserInputError):
        _run(tmp_path)
    assert list(tmp_path.rglob("*.json")) == []


# --- M7 / M2: one config() call, and stage states that mean what they say -----

def test_task_config_is_called_exactly_once_per_run(tmp_path):
    _run(tmp_path)
    assert CALLS.count("config") == 1


def test_skipped_means_deliberately_not_run_and_absent_means_never_reached(tmp_path):
    record = _run(tmp_path, preflight=False, enrich=False)
    assert record.run.stages["PREFLIGHT"] == "skipped"
    assert record.run.stages["ENRICH"] == "skipped"
    assert record.run.stages["PERSIST"] == "ok"
    assert "PUBLISH" not in record.run.stages


# --- I6: a TaskExecutionError is attributed to the stage that was running -----

def test_a_task_execution_error_during_setup_is_attributed_to_setup(
    tmp_path, monkeypatch
):
    partial = ObservationBundle(observations={"provisioned": False})

    def _boom(self):
        CALLS.append("setup")
        raise TaskExecutionError(
            "cluster never came up", observations=partial, code="setup_timeout"
        )

    monkeypatch.setattr(_Adapter, "setup", _boom)
    record = _run(tmp_path)

    assert record.status == "failed"
    assert record.run.stages["SETUP"] == "failed"
    assert [e["stage"] for e in record.errors] == ["SETUP"]
    assert record.errors[0]["code"] == "setup_timeout"
    assert record.observations == {"provisioned": False}
    assert CALLS.count("teardown") == 1


# --- non-canonical evidence: recorded, degraded, never crashed ---------------

def test_non_canonical_observations_fail_collect_and_are_still_persisted(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        _Task,
        "execute",
        lambda self, adapter, params: ObservationBundle(
            observations={"ratio": float("nan")}
        ),
    )
    record = _run(tmp_path)

    assert record.status == "failed"
    assert record.run.stages["COLLECT"] == "failed"
    assert record.run.stages["SCORE"] == "skipped"
    stages = [e["stage"] for e in record.errors]
    assert stages[0] == "COLLECT"
    assert "PERSIST" not in stages
    assert record.run.stages["PERSIST"] == "ok"

    files = [p for p in tmp_path.rglob("*.json")]
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "NaN" not in text
    data = json.loads(text)
    assert data["status"] == "failed"
    assert data["observations"] == {}
    assert record.observations == {}  # what we return is what we wrote


def test_a_datetime_observation_is_reported_at_collect(tmp_path, monkeypatch):
    from datetime import datetime

    monkeypatch.setattr(
        _Task,
        "execute",
        lambda self, adapter, params: ObservationBundle(
            observations={"when": datetime(2026, 7, 26)}
        ),
    )
    record = _run(tmp_path)

    assert record.status == "failed"
    assert record.run.stages["COLLECT"] == "failed"
    assert record.errors[0]["stage"] == "COLLECT"
    assert "datetime" in record.errors[0]["message"]


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("observations", ["not-a-mapping"]),
        ("series", ["not-a-mapping"]),
        ("artifacts", {"not": "a-list"}),
    ],
)
def test_invalid_observation_bundle_container_fails_collect_and_still_persists(
    tmp_path, monkeypatch, field, bad_value
):
    bundle = ObservationBundle()
    setattr(bundle, field, bad_value)
    monkeypatch.setattr(_Task, "execute", lambda self, adapter, params: bundle)

    record = _run(tmp_path)

    assert record.status == "failed"
    assert record.run.stages["COLLECT"] == "failed"
    assert record.run.stages["SCORE"] == "skipped"
    assert record.errors[0]["stage"] == "COLLECT"
    assert record.observations == {}
    assert record.series == {}
    assert record.artifacts == []
    assert _persisted(tmp_path)["status"] == "failed"


def test_malformed_partial_bundle_from_collect_error_cannot_escape_record_build(
    tmp_path, monkeypatch
):
    malformed = ObservationBundle()
    malformed.observations = {"ratio": float("nan")}
    malformed.series = {"latency_ms": [[1, float("nan")]]}
    malformed.artifacts = {"not": "a-list"}

    def _boom(bundle):
        raise TaskExecutionError(
            "collect failed",
            observations=malformed,
            code="collect_failed",
        )

    monkeypatch.setattr(orch, "collect", _boom)
    record = _run(tmp_path)

    assert record.status == "failed"
    assert record.run.stages["COLLECT"] == "failed"
    assert record.run.stages["PERSIST"] == "ok"
    assert [error["stage"] for error in record.errors] == ["COLLECT"]
    assert record.observations == {}
    assert record.series == {}
    assert record.artifacts == []
    persisted = _persisted(tmp_path)
    assert persisted["observations"] == {}
    assert persisted["series"] == {}
    assert persisted["artifacts"] == []


@pytest.mark.parametrize("bad_value", [object(), float("nan")])
def test_non_canonical_scored_measurement_fails_score_and_keeps_observations(
    tmp_path, monkeypatch, bad_value
):
    monkeypatch.setattr(
        _Task,
        "score",
        lambda self, observations: TaskResult(
            measurements={
                "bad": Measurement(value=bad_value, unit="", evidence="C")
            }
        ),
    )
    record = _run(tmp_path)

    assert record.status == "failed"
    assert record.run.stages["SCORE"] == "failed"
    assert record.observations == {"hits": 3}
    assert record.measurements == {}
    assert record.errors[0]["stage"] == "SCORE"


def test_numpy_like_scored_measurement_fails_score(tmp_path, monkeypatch):
    class NumpyLike:
        def __float__(self):
            return 1.0

    monkeypatch.setattr(
        _Task,
        "score",
        lambda self, observations: TaskResult(
            measurements={
                "bad": Measurement(value=NumpyLike(), unit="", evidence="C")
            }
        ),
    )
    record = _run(tmp_path)

    assert record.status == "failed"
    assert record.run.stages["SCORE"] == "failed"
    assert record.measurements == {}


# --- M3: a debug-log failure must never mask the error it was logging --------

def test_a_debug_log_failure_does_not_mask_the_stage_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        _Task,
        "execute",
        lambda self, adapter, params: (_ for _ in ()).throw(
            ConnectionError("session dropped")
        ),
    )
    (tmp_path / "debug").write_text("not a directory", encoding="utf-8")

    record = _run(tmp_path, debug=True)

    assert record.status == "failed"
    assert [e["stage"] for e in record.errors] == ["EXECUTE"]
    assert record.errors[0]["type"] == "ConnectionError"


# --- I9: a stage message must not carry the operator's machine identity ------

def test_stage_error_messages_are_scrubbed_of_the_operator_identity(
    tmp_path, monkeypatch
):
    user = getpass.getuser()
    monkeypatch.setattr(
        _Task,
        "execute",
        lambda self, adapter, params: (_ for _ in ()).throw(
            OSError(f"/Users/{user}/results/run.json: permission denied")
        ),
    )
    record = _run(tmp_path)

    assert user not in record.errors[0]["message"]
    assert user not in json.dumps(_persisted(tmp_path))
