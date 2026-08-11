# tests/test_aliyun_remote_probe_client.py
from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime.aliyun import AliyunAgentRunTransport


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
    t = AliyunAgentRunTransport(_Adapter({
        "region": "cn-hangzhou",
        "probe_url": "http://1.2.3.4:9000",
        "probe_oss_prefix": "campaign-1/job-1/",
        "probe_in_vpc": False,
        "endpoint_url": "http://runtime-under-test",
    }))
    assert captured["base_url"] == "http://1.2.3.4:9000"
    b = t.run_data_plane_probe("soak", {"duration_s": 0.1})
    # routed to the remote client, not run in-process
    assert captured["spec"].probe == "soak"
    assert captured["spec"].oss_prefix == "campaign-1/job-1/"
    assert captured["spec"].target_endpoint == "http://runtime-under-test"
    # vantage flipped to eci because a remote client is active
    assert b.observations["vantage"]["carrier"] == "eci"
    assert b.observations["vantage"]["in_vpc"] is False
