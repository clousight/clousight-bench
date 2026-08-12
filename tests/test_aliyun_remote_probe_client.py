# tests/test_aliyun_remote_probe_client.py
from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime.aliyun import AliyunAgentRunTransport
from clousight_bench.domains.agent_runtime.probe.oss_dispatch_client import OssProbeClient


class _Adapter:
    def __init__(self, target):
        self.target = target
        self.mock_base_url = "http://mock"
        self.run_id = None


def test_no_probe_url_leaves_client_none_and_vantage_local():
    t = AliyunAgentRunTransport(_Adapter({"region": "cn-hangzhou"}))
    assert t._probe_client is None


def test_probe_url_builds_remote_client_and_routes_run_job(monkeypatch):
    captured = {}

    class _FakeRemote:
        def __init__(self, base_url, *a, **k):
            captured["base_url"] = base_url

        def run_job(self, spec):
            captured["spec"] = spec
            return ObservationBundle(observations={"capability": "supported", "availability": 1.0})

    monkeypatch.setattr("clousight_bench.domains.agent_runtime.probe.client.RemoteProbeClient", _FakeRemote)
    t = AliyunAgentRunTransport(
        _Adapter(
            {
                "region": "cn-hangzhou",
                "probe_url": "http://1.2.3.4:9000",
                "probe_oss_prefix": "campaign-1/job-1/",
                "probe_in_vpc": False,
                "endpoint_url": "http://runtime-under-test",
            }
        )
    )
    assert captured["base_url"] == "http://1.2.3.4:9000"
    b = t.run_data_plane_probe("soak", {"duration_s": 0.1})
    # routed to the remote client, not run in-process
    assert captured["spec"].probe == "soak"
    assert captured["spec"].oss_prefix == "campaign-1/job-1/"
    assert captured["spec"].target_endpoint == "http://runtime-under-test"
    # vantage flipped to eci because a remote client is active
    assert b.observations["vantage"]["carrier"] == "eci"
    assert b.observations["vantage"]["in_vpc"] is False


def test_probe_control_prefix_builds_oss_probe_client(monkeypatch):
    """probe_control_prefix → OssProbeClient is selected (OSS-mediated path)."""
    from clousight_bench.domains.agent_runtime.probe.oss_client import InMemoryOssClient

    # Patch Oss2Client at the import site in aliyun.py so no real OSS creds are needed.
    class _FakeOss2Client(InMemoryOssClient):
        def __init__(self, bucket: str, region: str) -> None:
            super().__init__()

    monkeypatch.setattr(
        "clousight_bench.domains.agent_runtime.probe.oss_client.Oss2Client",
        _FakeOss2Client,
    )

    t = AliyunAgentRunTransport(
        _Adapter(
            {
                "region": "cn-hangzhou",
                "probe_control_prefix": "campaign-oss-test",
                "oss_bucket": "my-bench-bucket",
                "probe_in_vpc": True,
                "endpoint_url": "http://runtime-under-test",
            }
        )
    )

    # The OSS-mediated arm must install an OssProbeClient, not RemoteProbeClient.
    assert isinstance(t._probe_client, OssProbeClient)


def test_probe_control_prefix_vantage_in_vpc(monkeypatch):
    """When OssProbeClient is active, run_data_plane_probe reports in_vpc=True."""
    from clousight_bench.domains.agent_runtime.probe.oss_client import InMemoryOssClient

    class _FakeOss2Client(InMemoryOssClient):
        def __init__(self, bucket: str, region: str) -> None:
            super().__init__()

    monkeypatch.setattr(
        "clousight_bench.domains.agent_runtime.probe.oss_client.Oss2Client",
        _FakeOss2Client,
    )

    t = AliyunAgentRunTransport(
        _Adapter(
            {
                "region": "cn-hangzhou",
                "probe_control_prefix": "campaign-oss-test",
                "oss_bucket": "my-bench-bucket",
                "probe_oss_prefix": "campaign-oss-test/job-1/",
                "probe_in_vpc": True,
                "endpoint_url": "http://runtime-under-test",
            }
        )
    )

    # Stub run_job on the already-constructed OssProbeClient so no real OSS back-end
    # is needed while still exercising the vantage-metadata path.
    captured: dict = {}

    def _fake_run_job(spec):
        captured["spec"] = spec
        return ObservationBundle(observations={"capability": "supported", "availability": 1.0})

    t._probe_client.run_job = _fake_run_job

    b = t.run_data_plane_probe("soak", {"duration_s": 0.1})
    assert b.observations["vantage"]["carrier"] == "eci"
    assert b.observations["vantage"]["in_vpc"] is True
