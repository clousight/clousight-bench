from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime.probe.jobs import (
    JobProgress,
    JobRecord,
    JobSpec,
    new_job_id,
    observation_bundle_from_dict,
)


def test_jobspec_roundtrips_through_dict():
    spec = JobSpec(
        probe="ttft",
        params={"samples": 5},
        target_endpoint="https://x/ep",
        mock_base_url="https://mock",
        mock_token="t",
    )
    d = spec.to_dict()
    assert d["probe"] == "ttft" and d["params"] == {"samples": 5}
    # to_dict emits ONLY the new wire key; the legacy "oss_prefix" key is gone.
    assert "blob_prefix" in d and "oss_prefix" not in d
    assert JobSpec.from_dict(d) == spec


def test_jobspec_from_dict_applies_defaults():
    spec = JobSpec.from_dict({"probe": "ttft", "params": {}, "target_endpoint": "http://127.0.0.1:9000"})
    assert spec.session_header_scheme == "X-AgentRun-Session-ID"
    assert spec.mock_base_url == "" and spec.blob_prefix == ""


def test_jobspec_from_dict_dual_reads_legacy_oss_prefix():
    # Read-migration shim: a job blob written before the oss_prefix→blob_prefix
    # rename must still populate blob_prefix on read.
    spec = JobSpec.from_dict(
        {"probe": "ttft", "params": {}, "target_endpoint": "http://127.0.0.1:9000", "oss_prefix": "x"}
    )
    assert spec.blob_prefix == "x"
    # the new key wins when both are present
    spec2 = JobSpec.from_dict(
        {
            "probe": "ttft",
            "params": {},
            "target_endpoint": "http://127.0.0.1:9000",
            "oss_prefix": "old",
            "blob_prefix": "new",
        }
    )
    assert spec2.blob_prefix == "new"


def test_jobrecord_to_dict_nests_progress():
    rec = JobRecord(
        job_id="job-1",
        status="running",
        progress=JobProgress(phase="sample", completed=2, total=5, elapsed_s=1.5),
    )
    d = rec.to_dict()
    assert d["status"] == "running"
    assert d["progress"] == {"phase": "sample", "completed": 2, "total": 5, "elapsed_s": 1.5}
    assert d["observations"] is None


def test_new_job_id_is_unique_and_prefixed():
    a, b = new_job_id(), new_job_id()
    assert a.startswith("job-") and a != b


def test_observation_bundle_from_dict_rebuilds_bundle():
    original = ObservationBundle(
        observations={"capability": "supported", "ttft_ms": [1.0, 2.0]},
        series={"ttft_ms": [[1, 1.0], [2, 2.0]]},
    )
    rebuilt = observation_bundle_from_dict(original.to_dict())
    assert isinstance(rebuilt, ObservationBundle)
    assert rebuilt.observations == original.observations
    assert rebuilt.series == original.series
    assert rebuilt.artifacts == []
