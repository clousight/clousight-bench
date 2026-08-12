"""Agent-runtime tasks: execute observes, score concludes, score stays pure."""

from clousight_bench.core.observation import ObservationBundle, collect
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter
from clousight_bench.domains.agent_runtime.tasks.t1_2_state_persistence import (
    StatePersistenceTask,
)
from clousight_bench.domains.agent_runtime.tasks.t1_3_fault_recovery import (
    FaultRecoveryTask,
)
from clousight_bench.domains.agent_runtime.tasks.t1_11_concurrent_writes import (
    ConcurrentWritesTask,
)


def _run(task, adapter):
    adapter.setup()
    try:
        return collect(task.execute(adapter, {}))
    finally:
        adapter.teardown()


# ---------------------------------------------------------------------------
# T1.2 state persistence -- AgentRun has no native session state
# ---------------------------------------------------------------------------


def test_t1_2_execute_returns_unsupported_without_calling_state_api():
    # AgentRun (FC-based) has no native session state; execute must immediately
    # report unsupported without touching persist_state / resume_session.
    bundle = _run(StatePersistenceTask(), LocalSimAdapter({"state_persistence": "durable"}))
    assert isinstance(bundle, ObservationBundle)
    assert bundle.observations["capability"] == "unsupported"
    assert "reason" in bundle.observations
    # must NOT contain keys from the old "supported" path
    assert "recovered" not in bundle.observations
    assert "probe" not in bundle.observations


def test_t1_2_score_unsupported_uses_evidence_A_and_info_finding():
    task = StatePersistenceTask()
    bundle = _run(task, LocalSimAdapter())
    before = bundle.to_dict()
    first = task.score(bundle)
    second = task.score(bundle)
    # score() must be pure
    assert bundle.to_dict() == before
    assert first.unsupported is True
    assert first.measurements["state_capability"].value == "unsupported"
    assert first.measurements["state_capability"].evidence == "A"
    assert [f.code for f in first.findings] == ["agent_runtime.no_native_session_state"]
    assert first.findings[0].severity in ("info", "warning")
    # revisions bumped
    assert first.task_revision == "3" and first.scorer_revision == "3"
    # score is repeatable
    assert second.measurements["state_capability"].value == "unsupported"


def test_t1_2_score_unsupported_is_a_finding_not_a_crash():
    # Kept for backward-compat shape: unsupported=True, evidence A (was C)
    task = StatePersistenceTask()
    result = task.score(_run(task, LocalSimAdapter()))
    assert result.unsupported is True
    assert result.measurements["state_capability"].value == "unsupported"
    assert result.measurements["state_capability"].evidence == "A"
    assert [f.code for f in result.findings] == ["agent_runtime.no_native_session_state"]


# ---------------------------------------------------------------------------
# T1.11 concurrent writes -- AgentRun has no native session state
# ---------------------------------------------------------------------------


def test_t1_11_execute_returns_unsupported_without_calling_probe():
    # AgentRun has no native session state; execute must report unsupported
    # immediately without calling probe_concurrent_writes.
    bundle = _run(ConcurrentWritesTask(), LocalSimAdapter())
    assert isinstance(bundle, ObservationBundle)
    assert bundle.observations["capability"] == "unsupported"
    assert "reason" in bundle.observations
    # must NOT contain keys from the old "supported" path
    assert "write_safe" not in bundle.observations
    assert "winner" not in bundle.observations


def test_t1_11_score_unsupported_uses_evidence_A_and_info_finding():
    task = ConcurrentWritesTask()
    bundle = _run(task, LocalSimAdapter())
    before = bundle.to_dict()
    result = task.score(bundle)
    # score() must be pure
    assert bundle.to_dict() == before
    assert result.unsupported is True
    assert result.measurements["state_capability"].value == "unsupported"
    assert result.measurements["state_capability"].evidence == "A"
    assert [f.code for f in result.findings] == ["agent_runtime.no_native_session_state"]
    assert result.findings[0].severity in ("info", "warning")
    # revisions bumped
    assert result.task_revision == "2" and result.scorer_revision == "2"


def test_t1_3_execute_produces_new_shape():
    """execute() returns the new three-state observation shape."""
    task = FaultRecoveryTask()
    bundle = _run(task, LocalSimAdapter({"recovery": {"mode": "auto-retry"}}))
    o = bundle.observations
    assert o["capability"] == "supported"
    assert isinstance(o["recovered"], bool)
    assert isinstance(o["observed_attempts"], int)
    assert isinstance(o["recovery_ms"], float)
    assert isinstance(o["platform_terminated"], bool)
    # Old shape keys must NOT be present
    assert "plan_calls" not in o
    assert "attempts" not in o
    assert "recovery_mode" not in o


def test_t1_3_score_recovered_true_no_findings():
    """Local-sim auto-retry → recovered=True, no warning findings."""
    task = FaultRecoveryTask()
    result = task.score(_run(task, LocalSimAdapter({"recovery": {"mode": "auto-retry"}})))
    assert result.measurements["recovered"].value is True
    assert result.measurements["observed_attempts"].value >= 1
    assert result.measurements["recovery_ms"].unit == "ms"
    assert result.findings == []


def test_t1_3_score_platform_terminated_warning():
    """platform_terminated=True → warning finding."""
    task = FaultRecoveryTask()
    bundle = ObservationBundle(
        observations={
            "capability": "supported",
            "recovered": False,
            "observed_attempts": 1,
            "recovery_ms": 5000.0,
            "platform_terminated": True,
        }
    )
    result = task.score(bundle)
    assert [(f.code, f.severity) for f in result.findings] == [
        ("agent_runtime.platform_timeout_recovery", "warning")
    ]


def test_t1_3_score_platform_blocked_retry_warning():
    """recovered=False + observed_attempts≤1 + not terminated → platform_blocked_retry warning."""
    task = FaultRecoveryTask()
    bundle = ObservationBundle(
        observations={
            "capability": "supported",
            "recovered": False,
            "observed_attempts": 1,
            "recovery_ms": 10.0,
            "platform_terminated": False,
        }
    )
    result = task.score(bundle)
    assert [(f.code, f.severity) for f in result.findings] == [
        ("agent_runtime.platform_blocked_retry", "warning")
    ]


def test_environment_facts_are_declared_and_non_sensitive():
    adapter = LocalSimAdapter({"recovery": {"mode": "auto-retry"}})
    assert StatePersistenceTask().environment_facts(adapter, {}) == {"state_persistence_policy": "durable"}
    assert FaultRecoveryTask().environment_facts(adapter, {}) == {
        "recovery_policy": "auto-retry",
        "max_retries": 3,
    }


def test_t2_1_execute_records_each_registration_path_attempt():
    from clousight_bench.domains.agent_runtime.tasks.t2_1_tool_registration import (
        ToolRegistrationTask,
    )

    task = ToolRegistrationTask()
    bundle = _run(task, LocalSimAdapter({"tool_registration": ["mcp"]}))
    assert bundle.observations["support"] == {
        "mcp": True,
        "openapi": False,
        "native": False,
    }
    assert "supported_count" not in bundle.observations


def test_t2_1_score_counts_paths_and_flags_a_runtime_with_none():
    from clousight_bench.domains.agent_runtime.tasks.t2_1_tool_registration import (
        ToolRegistrationTask,
    )

    task = ToolRegistrationTask()
    result = task.score(_run(task, LocalSimAdapter({"tool_registration": ["mcp"]})))
    assert result.measurements["supported_paths"].value == ["mcp"]
    assert result.measurements["supported_count"].value == 1
    assert result.measurements["openapi"].value is False
    assert result.findings == []
    assert result.unsupported is False

    none_result = task.score(_run(task, LocalSimAdapter({"tool_registration": []})))
    assert none_result.unsupported is True
    assert [f.code for f in none_result.findings] == ["agent_runtime.no_tool_registration_path"]


def test_t4_1_score_flags_missing_span_kinds():
    from clousight_bench.domains.agent_runtime.tasks.t4_1_trace_completeness import (
        TraceCompletenessTask,
    )

    task = TraceCompletenessTask()
    full = task.score(_run(task, LocalSimAdapter()))
    assert full.measurements["span_completeness"].value == 1.0
    assert full.measurements["kinds_missing"].value == []
    assert full.findings == []

    partial = task.score(_run(task, LocalSimAdapter({"trace": {"completeness": "partial"}})))
    assert partial.measurements["span_completeness"].value < 1.0
    assert "TOOL" in partial.measurements["kinds_missing"].value
    assert [(f.code, f.severity) for f in partial.findings] == [
        ("agent_runtime.trace_spans_missing", "warning")
    ]


def test_t4_1_absent_trace_api_is_unsupported():
    from clousight_bench.domains.agent_runtime.adapters.base import CapabilityNotSupported
    from clousight_bench.domains.agent_runtime.tasks.t4_1_trace_completeness import (
        TraceCompletenessTask,
    )

    class _NoTrace(LocalSimAdapter):
        def get_trace(self, session_id):
            raise CapabilityNotSupported("get_trace")

    task = TraceCompletenessTask()
    result = task.score(_run(task, _NoTrace()))
    assert result.unsupported is True
    assert result.measurements["trace_capability"].value == "unsupported"
    assert result.measurements["span_completeness"].value == 0.0
    assert [f.code for f in result.findings] == ["agent_runtime.trace_api_absent"]


def test_t4_2_score_validates_the_otel_payload():
    from clousight_bench.domains.agent_runtime.tasks.t4_2_otel_export import OtelExportTask

    task = OtelExportTask()
    ok = task.score(_run(task, LocalSimAdapter()))
    assert ok.measurements["otel_export_supported"].value is True
    assert ok.measurements["otel_valid"].value is True
    assert ok.measurements["span_count"].value >= 1
    assert ok.findings == []

    absent = task.score(_run(task, LocalSimAdapter({"trace": {"otel_export": False}})))
    assert absent.unsupported is True
    assert absent.measurements["otel_export_supported"].value is False
    assert absent.measurements["otel_valid"].value is False
    assert [f.code for f in absent.findings] == ["agent_runtime.otel_export_absent"]
