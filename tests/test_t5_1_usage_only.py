from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime.tasks.t5_1_cost_attribution import (
    CostAttributionTask,
)


def test_score_reports_usage_only_no_cost():
    obs = ObservationBundle(
        observations={
            "invocations": 8,
            "vcpu_hours": 2e-6,
            "duration_ms": 7.1,
            "completed": True,
        }
    )
    result = CostAttributionTask().score(obs)
    assert "cost_usd" not in result.measurements
    assert result.measurements["invocations"].value == 8
    assert result.measurements["vcpu_hours"].value == 2e-6


def test_scorer_revision_bumped():
    assert CostAttributionTask.scorer_revision == "2"
