"""Cross-language workload protocol + big-data local baseline.

Proves the SAME lifecycle carries a non-agent product, and that the WorkloadEngine
parses the JSONL protocol from a subprocess.
"""

from clousight_bench.core.orchestrator import execute
from clousight_bench.core.resources import reference_workload_path
from clousight_bench.core.schema import RunSpec
from clousight_bench.core.workload import WorkloadEngine


def test_workload_engine_parses_jsonl_protocol():
    engine = WorkloadEngine(reference_workload_path("wordcount-py"))
    assert engine.name == "wordcount-py"
    result = engine.run({"rows": 1000, "seed": 7})
    assert result.ok
    assert result.metrics["rows_processed"] == 1000
    assert "throughput_rows_per_s" in result.metrics


def test_workload_identity_in_describe():
    engine = WorkloadEngine(reference_workload_path("wordcount-py"))
    desc = engine.describe()
    assert desc["workload"] == "wordcount-py"
    assert "declared_metrics" in desc


def test_bigdata_j1_1_local_process(tmp_path):
    spec = RunSpec(
        domain="bigdata-emr", task_id="J1.1", platform="local-process", params={"rows": 5000, "seed": 1}
    )
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    assert rec.measurements["job_succeeded"]["value"] is True
    assert rec.measurements["rows_processed"]["value"] == 5000
