# packages/cb-adapters-enterprise/tests/test_probe_oss_artifacts.py
from clousight_bench.core.observation import ObservationBundle, validate_observation_bundle
from clousight_bench.domains.agent_runtime.probe.oss_client import InMemoryOssClient
from clousight_bench.domains.agent_runtime.probe.oss_sink import OssChunkSink
from clousight_bench.domains.agent_runtime.probe.oss_sync import chunks_to_artifacts


def test_chunks_to_artifacts_pass_bundle_validation():
    c = InMemoryOssClient()
    sink = OssChunkSink(c, "campaign-x/job-y/", chunk_max_records=1)
    sink.append("series", {"t": 1, "v": 1.0})
    sink.append("spans", {"trace_id": "T1", "span_id": "a"})
    manifest = sink.close()
    arts = chunks_to_artifacts(manifest, bucket="clousight-bench-8388a7e6")
    kinds = sorted(a["kind"] for a in arts)
    assert kinds == ["probe-series", "probe-spans"]
    for a in arts:
        assert a["uri"].startswith("oss://clousight-bench-8388a7e6/campaign-x/job-y/")
        assert a["sha256"].startswith("sha256:") and a["media"] == "application/x-ndjson"
    # The whole point: these attach to a bundle and survive COLLECT validation.
    validate_observation_bundle(ObservationBundle(observations={"capability": "supported"}, artifacts=arts))
