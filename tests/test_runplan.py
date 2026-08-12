"""A run plan repeats a benchmark, discards warmups, and aggregates only
records that are the same benchmark in the same environment."""

import json

import pytest

from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    ResultRecord,
    RunInfo,
)
from clousight_bench.core.runplan import (
    AGGREGATES_DIRNAME,
    RunPlan,
    RunPlanError,
    build_aggregate,
    execute_plan,
)
from clousight_bench.core.schema import RunSpec

_SPEC = RunSpec("agent-runtime", "T1.3", "local-sim", target={"recovery": {"mode": "auto-retry"}})


def _record(
    run_id,
    *,
    benchmark="sha256:b",
    environment="sha256:e",
    implementation="sha256:i",
    status="completed",
    measurements=None,
):
    return ResultRecord(
        run=RunInfo(run_id, "2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z", {"PERSIST": "ok"}),
        identity=Identity("agent-runtime", "T1.3", "1", "1", "local-sim", "reference", "0.2.0"),
        environment=Environment("", "local", "3.12.0", "Linux"),
        fingerprints=Fingerprints(benchmark, environment, implementation, "sha256:d"),
        status=status,
        measurements=measurements or {"lat": {"value": 1.0, "unit": "ms", "evidence": "C"}},
    )


def test_repeat_and_warmup_counts_are_validated():
    with pytest.raises(RunPlanError, match="repeat"):
        RunPlan(_SPEC, repeat=0)
    with pytest.raises(RunPlanError, match="warmup"):
        RunPlan(_SPEC, repeat=1, warmup=-1)


def test_execute_plan_persists_every_run_and_excludes_warmups(tmp_path):
    aggregate = execute_plan(RunPlan(_SPEC, repeat=3, warmup=2), results_dir=tmp_path)

    # Every single run — warmup and measured — is its own auditable record.
    records = list((tmp_path / "agent-runtime" / "local-sim").glob("*.json"))
    assert len(records) == 5

    assert len(aggregate.runs["warmup"]) == 2
    assert len(aggregate.runs["measured"]) == 3
    # Statistics pool only the 3 measured runs.
    assert aggregate.measurements["observed_attempts"]["n"] == 3
    assert aggregate.status_counts == {"completed": 3}
    assert aggregate.comparable is True


def test_each_run_is_tagged_with_its_plan_role(tmp_path):
    execute_plan(RunPlan(_SPEC, repeat=1, warmup=1), results_dir=tmp_path, plan_id="plan-fixed")
    roles = []
    for path in (tmp_path / "agent-runtime" / "local-sim").glob("*.json"):
        plan = json.loads(path.read_text())["extensions"]["core"]["run_plan"]
        assert plan["plan_id"] == "plan-fixed"
        roles.append(plan["role"])
    assert sorted(roles) == ["measured", "warmup"]


def test_warmup_and_measured_share_the_same_benchmark_fingerprint(tmp_path):
    execute_plan(RunPlan(_SPEC, repeat=2, warmup=1), results_dir=tmp_path)
    benchmarks = set()
    for path in (tmp_path / "agent-runtime" / "local-sim").glob("*.json"):
        benchmarks.add(json.loads(path.read_text())["fingerprints"]["benchmark"])
    assert len(benchmarks) == 1  # a warmup is the same benchmark, not another one


def test_the_aggregate_is_persisted_with_its_own_digest(tmp_path):
    aggregate = execute_plan(RunPlan(_SPEC, repeat=2), results_dir=tmp_path, plan_id="plan-agg")
    path = tmp_path / AGGREGATES_DIRNAME / "agent-runtime" / "local-sim" / "T1.3-plan-agg.json"
    assert path.is_file()
    payload = json.loads(path.read_text())
    assert payload["kind"] == "run_plan_aggregate"
    assert payload["digest"].startswith("sha256:")
    assert payload == aggregate.to_dict()


def test_build_aggregate_only_pools_completed_and_unsupported_runs():
    plan = RunPlan(_SPEC, repeat=3)
    measured = [
        _record("r1", measurements={"lat": {"value": 10.0, "unit": "ms", "evidence": "C"}}),
        _record("r2", status="failed"),
        _record("r3", measurements={"lat": {"value": 20.0, "unit": "ms", "evidence": "C"}}),
    ]
    aggregate = build_aggregate("plan-x", plan, [], measured)
    assert aggregate.status_counts == {"completed": 2, "failed": 1}
    assert aggregate.measurements["lat"]["n"] == 2  # the failed run has no verdict
    assert aggregate.measurements["lat"]["mean"] == 15.0


def test_a_benchmark_that_changes_mid_plan_is_flagged_not_blended():
    plan = RunPlan(_SPEC, repeat=3)
    measured = [
        _record("r1", benchmark="sha256:AAA"),
        _record("r2", benchmark="sha256:AAA"),
        _record("r3", benchmark="sha256:BBB"),
    ]
    aggregate = build_aggregate("plan-y", plan, [], measured)
    assert aggregate.comparable is False
    assert aggregate.fingerprints["benchmark"] == "sha256:AAA"  # the larger group
    assert aggregate.measurements["lat"]["n"] == 2
    assert any("changed across repeats" in note for note in aggregate.notes)


def test_same_benchmark_but_changed_code_is_a_caveat_not_a_split():
    plan = RunPlan(_SPEC, repeat=2)
    measured = [
        _record("r1", implementation="sha256:CODE1"),
        _record("r2", implementation="sha256:CODE2"),
    ]
    aggregate = build_aggregate("plan-z", plan, [], measured)
    assert aggregate.comparable is True  # same benchmark + environment
    assert aggregate.fingerprints["implementation"] == "mixed"
    assert any("implementation fingerprint varies" in note for note in aggregate.notes)
    assert aggregate.measurements["lat"]["n"] == 2


def test_a_plan_where_everything_failed_aggregates_nothing_and_says_so():
    plan = RunPlan(_SPEC, repeat=2)
    measured = [_record("r1", status="failed"), _record("r2", status="invalid")]
    aggregate = build_aggregate("plan-f", plan, [], measured)
    assert aggregate.measurements == {}
    assert "no completed runs to aggregate" in aggregate.notes
