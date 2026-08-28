"""chunks_to_artifacts: sink manifest → ObservationBundle.artifacts entries."""

from clousight_bench.core.blobstore import InMemoryBlobStore
from clousight_bench.core.observation import ObservationBundle, validate_observation_bundle
from clousight_bench.domains.agent_runtime.probe.blob_sink import BlobChunkSink
from clousight_bench.domains.agent_runtime.probe.blob_sync import chunks_to_artifacts


def _close_sink() -> dict:
    c = InMemoryBlobStore()
    sink = BlobChunkSink(c, "campaign-x/job-y/", chunk_max_records=1)
    sink.append("series", {"t": 1, "v": 1.0})
    sink.append("spans", {"trace_id": "T1", "span_id": "a"})
    return sink.close()


def test_chunks_to_artifacts_pass_bundle_validation():
    manifest = _close_sink()
    arts = chunks_to_artifacts(manifest, bucket="test-bucket", scheme="oss")
    kinds = sorted(a["kind"] for a in arts)
    assert kinds == ["probe-series", "probe-spans"]
    for a in arts:
        assert a["uri"].startswith("oss://test-bucket/campaign-x/job-y/")
        assert a["sha256"].startswith("sha256:") and a["media"] == "application/x-ndjson"
    # The whole point: these attach to a bundle and survive COLLECT validation.
    validate_observation_bundle(ObservationBundle(observations={"capability": "supported"}, artifacts=arts))


def test_chunks_to_artifacts_uses_caller_supplied_scheme():
    manifest = _close_sink()
    arts = chunks_to_artifacts(manifest, bucket="test-bucket", scheme="s3")
    for a in arts:
        assert a["uri"].startswith("s3://test-bucket/campaign-x/job-y/")
    validate_observation_bundle(ObservationBundle(observations={"capability": "supported"}, artifacts=arts))
