"""Item 1: a cumulative cost budget that stops runs once spend crosses a threshold.

The live gate stops *accidental* spend; this stops *runaway* spend. A budget
(``--cost-budget`` / ``CSBENCH_COST_BUDGET`` / ``target.cost_budget``) caps the
total realized cost across runs sharing a results dir. Before a live run, if the
spent-so-far plus this run's estimate would cross the budget, the run is blocked
(``cost.budget_exceeded``) before anything is provisioned; after a run, its
realized cost (priced by the enricher, else ``target.estimated_cost_usd``) is
added to the ledger.
"""

from clousight_bench.core.cost_budget import (
    CostLedger,
    budget_would_exceed,
    run_cost_usd,
)
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec
from clousight_bench.domains.agent_runtime.adapters.cn_clouds import AliyunAgentRunAdapter

# --- unit -------------------------------------------------------------------


def test_ledger_accumulates_and_persists(tmp_path):
    led = CostLedger(tmp_path)
    led.add("run-1", "aliyun", 0.5)
    led.add("run-2", "aliyun", 0.75)
    assert led.total() == 1.25
    # a fresh ledger over the same dir sees the persisted total
    assert CostLedger(tmp_path).total() == 1.25


def test_run_cost_prefers_priced_cost(tmp_path):
    cost = run_cost_usd(_rec_with_pricing(0.42), target={"estimated_cost_usd": 9.0})
    assert cost == 0.42  # priced cost wins over the estimate


def test_run_cost_falls_back_to_estimate(tmp_path):
    cost = run_cost_usd(_rec_with_pricing(None), target={"estimated_cost_usd": 3.0})
    assert cost == 3.0


def test_budget_would_exceed():
    assert budget_would_exceed(spent=1.0, estimate=1.0, budget=1.5) is True
    assert budget_would_exceed(spent=0.0, estimate=1.0, budget=1.5) is False
    assert budget_would_exceed(spent=9.0, estimate=9.0, budget=None) is False  # no budget


# --- integration ------------------------------------------------------------


def test_second_live_run_is_blocked_when_budget_would_be_crossed(tmp_path, monkeypatch):
    monkeypatch.setattr(AliyunAgentRunAdapter, "execution_mode", lambda self: "live")
    target = {"mode": "mock", "cost_budget": 1.5, "estimated_cost_usd": 1.0}
    spec = RunSpec("agent-runtime", "T1.3", "aliyun-agentrun", target=target)

    rec1 = execute(spec, results_dir=tmp_path, preflight=False, allow_live=True)
    assert rec1.status == "completed"  # spent 0 + est 1 < 1.5 -> runs

    rec2 = execute(spec, results_dir=tmp_path, preflight=False, allow_live=True)
    # spent 1 + est 1 = 2 >= 1.5 -> blocked before provisioning
    assert rec2.status == "invalid"
    assert any(f["code"] == "cost.budget_exceeded" for f in rec2.findings)
    assert rec2.run.stages.get("SETUP") != "ok"


def _rec_with_pricing(cost):
    class _R:
        extensions = {"pricing": {"cost_usd": cost}} if cost is not None else {}

    return _R()
