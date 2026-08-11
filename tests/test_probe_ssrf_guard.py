"""JobSpec refuses SSRF targets (cloud metadata / link-local / non-http)."""
import pytest

from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec


@pytest.mark.parametrize("bad", [
    "http://100.100.100.200/latest/meta-data/",   # Aliyun metadata
    "http://169.254.169.254/",                     # AWS/GCP metadata + link-local
    "http://169.254.10.5/x",                       # link-local range
    "ftp://example.com/x",                         # non-http scheme
    "file:///etc/passwd",
])
def test_from_dict_rejects_unsafe_target_endpoint(bad):
    with pytest.raises(ValueError):
        JobSpec.from_dict({"probe": "ttft", "target_endpoint": bad})


def test_from_dict_rejects_unsafe_mock_base_url():
    with pytest.raises(ValueError):
        JobSpec.from_dict({"probe": "ttft", "target_endpoint": "https://ok.example.com",
                           "mock_base_url": "http://100.100.100.200/"})


@pytest.mark.parametrize("ok", [
    "https://agentrun.cn-hangzhou.aliyuncs.com",   # legit public endpoint
    "http://127.0.0.1:9000",                        # local-sim / tests
    "https://10.0.0.5:8443",                        # in-region VPC (private, allowed)
])
def test_from_dict_allows_legit_endpoints(ok):
    spec = JobSpec.from_dict({"probe": "ttft", "target_endpoint": ok})
    assert spec.target_endpoint == ok
