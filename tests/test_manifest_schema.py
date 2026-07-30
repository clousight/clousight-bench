import pytest

from clousight_bench.core.workload import WorkloadEngine, WorkloadError


def test_manifest_missing_entrypoint_rejected(tmp_path):
    (tmp_path / "manifest.yaml").write_text("name: x\nversion: 1\n", encoding="utf-8")
    with pytest.raises(WorkloadError):
        WorkloadEngine(tmp_path)


def test_manifest_not_a_mapping_rejected(tmp_path):
    (tmp_path / "manifest.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(WorkloadError):
        WorkloadEngine(tmp_path)


def test_good_manifest_loads(tmp_path):
    (tmp_path / "manifest.yaml").write_text(
        "name: w\nversion: 0.1.0\nentrypoint: ./run.sh\n", encoding="utf-8"
    )
    eng = WorkloadEngine(tmp_path)
    assert eng.name == "w"
