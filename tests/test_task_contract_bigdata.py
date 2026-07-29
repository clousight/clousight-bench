"""J1.1: workload metrics become measurements; a failed job becomes a finding."""

from clousight_bench.core.observation import ObservationBundle, collect
from clousight_bench.domains.bigdata_emr.adapters.local_process import (
    LocalProcessAdapter,
)
from clousight_bench.domains.bigdata_emr.tasks.j1_1_wordcount import (
    WordcountSmokeTask,
)


def test_execute_returns_raw_workload_output():
    task = WordcountSmokeTask()
    bundle = collect(task.execute(LocalProcessAdapter(), {"rows": 100, "seed": 7}))
    assert isinstance(bundle, ObservationBundle)
    assert bundle.observations["workload"] == "wordcount-py"
    assert bundle.observations["job_params"] == {"rows": 100, "seed": 7}
    assert bundle.observations["ok"] is True
    assert bundle.observations["raw_metrics"]["rows_processed"] == 100
    assert "job_succeeded" not in bundle.observations


def test_score_promotes_every_workload_metric_to_a_measurement():
    task = WordcountSmokeTask()
    bundle = collect(task.execute(LocalProcessAdapter(), {"rows": 100, "seed": 7}))
    result = task.score(bundle)
    assert result.measurements["rows_processed"].value == 100
    assert result.measurements["rows_processed"].evidence == "C"
    assert result.measurements["job_succeeded"].value is True
    assert result.findings == []
    assert result.task_revision == "2"


def test_score_reports_a_failed_job_as_a_critical_finding():
    result = WordcountSmokeTask().score(
        ObservationBundle(
            observations={
                "workload": "wordcount-py",
                "job_params": {},
                "raw_metrics": {},
                "exit_code": 1,
                "ok": False,
                "logs": ["boom"],
            }
        )
    )
    assert result.measurements["job_succeeded"].value is False
    assert [(finding.code, finding.severity) for finding in result.findings] == [
        ("bigdata.job_failed", "critical")
    ]
    assert result.findings[0].details["exit_code"] == 1


def test_workload_identity_names_the_packaged_workload():
    identity = WordcountSmokeTask().workload_identity({})
    assert identity["workload"] == "wordcount-py"
    assert identity["workload_version"]
    assert isinstance(identity["assets"], list)


def test_environment_facts_declare_the_workload():
    facts = WordcountSmokeTask().environment_facts(LocalProcessAdapter(), {})
    assert facts == {"workload": "wordcount-py"}
