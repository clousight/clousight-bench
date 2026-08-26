"""Resuming an interrupted campaign reuses completed runs and only re-runs the
missing/interrupted slots, so a long plan survives an interruption."""

import clousight_bench.core.runplan as rp
from clousight_bench.core.runplan import RunPlan, execute_plan
from clousight_bench.core.schema import RunSpec


def _spec():
    return RunSpec("agent-runtime", "stub.ok", "local-sim", target={"recovery": {"mode": "auto-retry"}})


def _count_executes(monkeypatch) -> dict[str, int]:
    """Spy that counts real execute() calls (records still land on disk)."""
    calls = {"n": 0}
    real = rp.execute

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(rp, "execute", counting)
    return calls


def test_resume_skips_all_completed_runs(tmp_path, monkeypatch):
    agg1 = execute_plan(RunPlan(_spec(), repeat=2), results_dir=tmp_path, plan_id="p1")
    assert len(agg1.runs["measured"]) == 2

    calls = _count_executes(monkeypatch)
    agg2 = execute_plan(RunPlan(_spec(), repeat=2), results_dir=tmp_path, plan_id="p1", resume=True)
    assert calls["n"] == 0, "all slots should be resumed from disk, nothing re-run"
    assert set(agg2.runs["measured"]) == set(agg1.runs["measured"])


def test_resume_runs_only_missing_slots(tmp_path, monkeypatch):
    # A plan interrupted after 1 of 2 repeats: resume runs exactly the 1 missing.
    execute_plan(RunPlan(_spec(), repeat=1), results_dir=tmp_path, plan_id="p2")

    calls = _count_executes(monkeypatch)
    agg = execute_plan(RunPlan(_spec(), repeat=2), results_dir=tmp_path, plan_id="p2", resume=True)
    assert calls["n"] == 1, "only the missing repeat should run"
    assert len(agg.runs["measured"]) == 2


def test_resume_requires_a_plan_id(tmp_path):
    import pytest

    from clousight_bench.core.runplan import RunPlanError

    with pytest.raises(RunPlanError, match="resume needs the plan_id"):
        execute_plan(RunPlan(_spec(), repeat=1), results_dir=tmp_path, resume=True)
