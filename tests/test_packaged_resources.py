import pytest

from clousight_bench.core.errors import UserInputError
from clousight_bench.core.resources import reference_workload_path
from clousight_bench.core.workload import WorkloadEngine


def test_reference_workloads_are_package_resources():
    for name in ("wordcount-py", "gsm8k-stats", "ycsb-wrapper"):
        path = reference_workload_path(name)
        assert (path / "manifest.yaml").is_file()


def test_packaged_wordcount_executes():
    engine = WorkloadEngine(reference_workload_path("wordcount-py"))
    result = engine.run({"rows": 100, "seed": 7})
    assert result.ok
    assert result.metrics["rows_processed"] == 100


def test_reference_workload_rejects_invalid_simple_name():
    with pytest.raises(UserInputError):
        reference_workload_path("../wordcount-py")


def test_reference_workload_rejects_unknown_name():
    with pytest.raises(UserInputError):
        reference_workload_path("does-not-exist")
