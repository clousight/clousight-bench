"""Agent-runtime tasks: execute observes, score concludes, score stays pure."""

from clousight_bench.core.observation import ObservationBundle, collect
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter
from clousight_bench.domains.agent_runtime.tasks.t1_2_state_persistence import (
    StatePersistenceTask,
)
from clousight_bench.domains.agent_runtime.tasks.t1_3_fault_recovery import (
    FaultRecoveryTask,
)


def _run(task, adapter):
    adapter.setup()
    try:
        return collect(task.execute(adapter, {}))
    finally:
        adapter.teardown()


def test_t1_2_execute_returns_raw_observations_without_a_verdict():
    bundle = _run(StatePersistenceTask(), LocalSimAdapter({"state_persistence": "durable"}))
    assert isinstance(bundle, ObservationBundle)
    assert bundle.observations["capability"] == "supported"
    assert bundle.observations["recovered"] == bundle.observations["probe"]
    assert "state_persisted" not in bundle.observations
    assert "persistence_mode" not in bundle.observations


def test_t1_2_score_is_pure_and_repeatable():
    task = StatePersistenceTask()
    bundle = _run(task, LocalSimAdapter({"state_persistence": "ephemeral"}))
    before = bundle.to_dict()
    first = task.score(bundle)
    second = task.score(bundle)
    assert bundle.to_dict() == before
    assert first.measurements["persistence_mode"].value == "ephemeral"
    assert second.measurements["persistence_mode"].value == "ephemeral"
    assert [f.code for f in first.findings] == ["agent_runtime.state_ephemeral"]
    assert first.task_revision == "2" and first.scorer_revision == "2"


def test_t1_2_unsupported_capability_is_a_finding_not_a_crash():
    class _NoState(LocalSimAdapter):
        def persist_state(self, session_id, state):
            from clousight_bench.domains.agent_runtime.adapters.base import (
                CapabilityNotSupported,
            )

            raise CapabilityNotSupported("persist_state")

    task = StatePersistenceTask()
    result = task.score(_run(task, _NoState()))
    assert result.unsupported is True
    assert result.measurements["state_capability"].value == "unsupported"
    assert [f.code for f in result.findings] == ["agent_runtime.state_api_absent"]


def test_t1_3_execute_records_every_attempt():
    task = FaultRecoveryTask()
    bundle = _run(task, LocalSimAdapter({"recovery": {"mode": "auto-retry"}}))
    assert bundle.observations["plan_calls"] == 5
    assert len(bundle.observations["attempts"]) >= 5
    assert set(bundle.observations["attempts"][0]) == {
        "call_index",
        "attempt",
        "status",
        "ok",
        "latency_ms",
    }
    assert "recovery_mode" not in bundle.observations


def test_t1_3_score_classifies_auto_retry():
    task = FaultRecoveryTask()
    result = task.score(_run(task, LocalSimAdapter({"recovery": {"mode": "auto-retry"}})))
    assert result.measurements["recovery_mode"].value == "auto-retry"
    assert result.measurements["budgeted_success"].value is True
    assert result.measurements["time_to_recovery_ms"].unit == "ms"
    assert result.findings == []


def test_t1_3_score_classifies_fail_fast_as_a_warning_finding():
    task = FaultRecoveryTask()
    result = task.score(_run(task, LocalSimAdapter({"recovery": {"mode": "fail-fast"}})))
    assert result.measurements["recovery_mode"].value == "fail-fast"
    assert result.measurements["final_state"].value == "aborted"
    assert [(f.code, f.severity) for f in result.findings] == [
        ("agent_runtime.recovery_fail_fast", "warning")
    ]


def test_t1_3_score_flags_a_missing_fault_as_critical():
    task = FaultRecoveryTask()
    bundle = ObservationBundle(
        observations={
            "fault": {},
            "plan_calls": 5,
            "completed": True,
            "final_state": "completed",
            "attempts": [
                {
                    "call_index": 1,
                    "attempt": 1,
                    "status": 200,
                    "ok": True,
                    "latency_ms": 1.0,
                }
            ],
        }
    )
    result = task.score(bundle)
    assert result.measurements["recovery_mode"].value == "no-fault-observed"
    assert [(f.code, f.severity) for f in result.findings] == [
        ("agent_runtime.fault_not_observed", "critical")
    ]


def test_environment_facts_are_declared_and_non_sensitive():
    adapter = LocalSimAdapter({"recovery": {"mode": "auto-retry"}})
    assert StatePersistenceTask().environment_facts(adapter, {}) == {
        "state_persistence_policy": "durable"
    }
    assert FaultRecoveryTask().environment_facts(adapter, {}) == {
        "recovery_policy": "auto-retry",
        "max_retries": 3,
    }
